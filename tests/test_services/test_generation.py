"""GenerationService 单元测试。"""

from unittest.mock import AsyncMock

import httpx
import pytest

from config import DingtalkConfig, PromptTableConfig, TableConfig
from services.generation import GenerationService


@pytest.fixture
def mock_dingtalk():
    return AsyncMock()


@pytest.fixture
def mock_generator():
    return AsyncMock()


@pytest.fixture
def service(mock_settings, mock_dingtalk, mock_generator):
    return GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=mock_settings,
    )


@pytest.fixture
def batch_settings(mock_settings):
    """含一张 batch_mode 表格的 Settings（不触发 _validate_batch_mode_config）。"""
    batch_table = TableConfig(
        key="batch-test",
        base_id="tbl_batch",
        sheet_id="sheet_image",
        image_api_key_env="ZHUOZHI_IMAGE_API_KEY",
        batch_mode=True,
        task_name="动作图-A",
        prompt_table_sheet_id="sheet_prompt",
        prompt_table=PromptTableConfig(),
        model_field="生图模型",
        reference_image_field="模特标准图",
        result_image_field="AI模特动作图",
        result_status_field="动作图状态",
        result_time_field="动作图执行时间",
    )
    new_dt = DingtalkConfig(
        default_table=mock_settings.dingtalk.default_table,
        tables=[*mock_settings.dingtalk.tables, batch_table],
        video_tables=mock_settings.dingtalk.video_tables,
    )
    mock_settings.dingtalk = new_dt
    return mock_settings


@pytest.mark.asyncio
async def test_process_success(service, mock_dingtalk, mock_generator):
    """完整成功流程。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "a cute cat",
            "素材图": [{"url": "/file/test.jpg", "filename": "test.jpg"}],
            "生图模型": {"id": "opt_banana_pro", "name": "Nano Banana Pro"},
        },
    }
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "generated_rec_001.png",
        "size": 1234,
        "type": "image/png",
        "url": "/resource/gen.png",
        "resourceId": "res_001",
    }
    mock_generator.generate.return_value = b"generated_png_bytes"

    await service.process(record_id="rec_001")

    # 验证流程被正确调用
    mock_dingtalk.get_record.assert_awaited_once()
    mock_dingtalk.download_file.assert_awaited_once_with("/file/test.jpg")
    mock_generator.generate.assert_awaited_once_with(
        model="Nano Banana Pro",
        prompt="a cute cat",
        reference_image=b"fake_image_bytes",
        table_config=mock_dingtalk.get_record.call_args[0][0],
    )
    mock_dingtalk.upload_attachment.assert_awaited_once()
    mock_dingtalk.update_record.assert_awaited_once()

    # 验证回写成功状态
    call_kwargs = mock_dingtalk.update_record.call_args
    fields = call_kwargs[0][2]
    assert fields["生成结果"] == "成功"
    assert fields["生成图片"] == [
        {
            "filename": "generated_rec_001.png",
            "size": 1234,
            "type": "image/png",
            "url": "/resource/gen.png",
            "resourceId": "res_001",
        }
    ]
    assert "生成时间" in fields


@pytest.mark.asyncio
async def test_process_missing_prompt(service, mock_dingtalk, mock_generator):
    """提示词为空时回写失败。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "",
            "素材图": [{"url": "/file/test.jpg"}],
        },
    }

    await service.process(record_id="rec_001")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "失败: 提示词不能为空"
    mock_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_missing_reference_image(service, mock_dingtalk, mock_generator):
    """素材图为空时回写失败。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "a cat",
            "素材图": None,
        },
    }

    await service.process(record_id="rec_001")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "失败: 素材图不能为空"
    mock_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_ai_generation_failure(service, mock_dingtalk, mock_generator):
    """AI 生图失败时回写失败。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "a cat",
            "素材图": [{"url": "/file/test.jpg"}],
            "生图模型": {"id": "opt_banana_2", "name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_generator.generate.side_effect = httpx.TimeoutException("AI 超时")

    await service.process(record_id="rec_001")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "失败" in fields["生成结果"]


@pytest.mark.asyncio
async def test_process_with_table_key(service, mock_dingtalk, mock_generator):
    """指定 table_key 时使用对应表格配置。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "a cat",
            "素材图": [{"url": "/file/test.jpg"}],
            "生图模型": {"id": "opt_banana_2", "name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "gen.png",
        "size": 1,
        "type": "image/png",
        "url": "/u",
        "resourceId": "r",
    }
    mock_generator.generate.return_value = b"png"

    await service.process(record_id="rec_001", table_key="clothing")

    # 验证用 clothing 表格配置调用了 get_record
    table_config = mock_dingtalk.get_record.call_args[0][0]
    assert table_config.key == "clothing"


@pytest.mark.asyncio
async def test_process_with_default_model(service, mock_dingtalk, mock_generator):
    """生图模型字段为空时使用 default_model。"""
    mock_dingtalk.get_record.return_value = {
        "id": "rec_001",
        "fields": {
            "提示词": "a cat",
            "素材图": [{"url": "/file/test.jpg"}],
            # 没有生图模型字段
        },
    }
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "gen.png",
        "size": 1,
        "type": "image/png",
        "url": "/u",
        "resourceId": "r",
    }
    mock_generator.generate.return_value = b"png"

    await service.process(record_id="rec_001")

    # 验证使用了 default_model ("Nano Banana 2")
    mock_generator.generate.assert_awaited_once_with(
        model="Nano Banana 2",
        prompt="a cat",
        reference_image=b"fake_image_bytes",
        table_config=mock_dingtalk.get_record.call_args[0][0],
    )


# ─────────── 批量生图（batch_mode=true）───────────


def _make_batch_service(batch_settings, mock_dingtalk, mock_generator):
    return GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=batch_settings,
    )


@pytest.mark.asyncio
async def test_batch_full_success(batch_settings, mock_dingtalk, mock_generator):
    """完整批量成功：3 张图 → upload × 3，update_record 收到 3 个 attachment。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_001",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg", "filename": "ref.jpg"}],
            "生图模型": {"name": "Nano Banana Pro"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {
            "id": "prompt_rec",
            "fields": {
                "提示词": "动作图-A base",
                "生成数量": 3,
                "扰动列表": "a\nb\nc",
            },
        }
    ]
    mock_dingtalk.download_file.return_value = b"ref_bytes"
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": "f1.png", "size": 1, "type": "image/png", "url": "/u1", "resourceId": "r1"},
        {"filename": "f2.png", "size": 1, "type": "image/png", "url": "/u2", "resourceId": "r2"},
        {"filename": "f3.png", "size": 1, "type": "image/png", "url": "/u3", "resourceId": "r3"},
    ]
    mock_generator.generate_batch.return_value = [b"img1", b"img2", b"img3"]

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_001", table_key="batch-test")

    # generate_batch 应收到 3 个 prompt
    mock_generator.generate_batch.assert_awaited_once()
    call_kwargs = mock_generator.generate_batch.call_args
    assert len(call_kwargs.kwargs["prompts"]) == 3
    assert call_kwargs.kwargs["model"] == "Nano Banana Pro"
    assert call_kwargs.kwargs["reference_image"] == b"ref_bytes"

    # upload × 3
    assert mock_dingtalk.upload_attachment.await_count == 3
    # update_record 收到 3 个附件
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert len(fields["AI模特动作图"]) == 3
    assert fields["动作图状态"] == "成功3/3"
    assert "动作图执行时间" in fields


@pytest.mark.asyncio
async def test_batch_partial_failure(batch_settings, mock_dingtalk, mock_generator):
    """部分失败：3 张里 1 张 None → upload × 2，状态含成功计数。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_002",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"提示词": "p", "生成数量": 3, "扰动列表": "a\nb\nc"}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": "f1.png", "size": 1, "type": "image/png", "url": "/u1", "resourceId": "r1"},
        {"filename": "f2.png", "size": 1, "type": "image/png", "url": "/u2", "resourceId": "r2"},
    ]
    mock_generator.generate_batch.return_value = [b"img1", None, b"img3"]

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_002", table_key="batch-test")

    assert mock_dingtalk.upload_attachment.await_count == 2
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert len(fields["AI模特动作图"]) == 2
    assert fields["动作图状态"] == "成功2/3"


@pytest.mark.asyncio
async def test_batch_all_failure(batch_settings, mock_dingtalk, mock_generator):
    """全失败：generate_batch 全部 None → 走 _update_failure 路径。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_003",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"提示词": "p", "生成数量": 2}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    mock_generator.generate_batch.return_value = [None, None]

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_003", table_key="batch-test")

    # 全失败 → 不应 upload，也不调 update_record（_update_failure 走 update_record）
    mock_dingtalk.upload_attachment.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "失败" in fields["动作图状态"]
    assert "2" in fields["动作图状态"]


@pytest.mark.asyncio
async def test_batch_missing_reference_image(batch_settings, mock_dingtalk, mock_generator):
    """素材图缺失 → 失败"素材图不能为空"。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_004",
        "fields": {"模特标准图": None},
    }

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_004", table_key="batch-test")

    mock_generator.generate_batch.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "素材图不能为空" in fields["动作图状态"]


@pytest.mark.asyncio
async def test_batch_prompt_table_not_found(batch_settings, mock_dingtalk, mock_generator):
    """提示词表无匹配 → 失败"提示词表未找到 任务名称=X"。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_005",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.list_records.return_value = []

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_005", table_key="batch-test")

    mock_generator.generate_batch.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "提示词表未找到 任务名称=动作图-A" in fields["动作图状态"]


@pytest.mark.asyncio
async def test_batch_empty_prompt_field(batch_settings, mock_dingtalk, mock_generator):
    """提示词为空 → 失败"提示词表的提示词为空"。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_006",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.list_records.return_value = [{"fields": {"提示词": ""}}]

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_006", table_key="batch-test")

    mock_generator.generate_batch.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "提示词表的提示词为空" in fields["动作图状态"]


@pytest.mark.asyncio
async def test_batch_perturbation_reuse(batch_settings, mock_dingtalk, mock_generator):
    """扰动复用：perts=[a], count=3 → 3 prompt 都含 '\\na'。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recB_007",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"提示词": "base", "生成数量": 3, "扰动列表": "a"}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "f.png", "size": 1, "type": "image/png", "url": "/u", "resourceId": "r"
    }
    mock_generator.generate_batch.return_value = [b"img1", b"img2", b"img3"]

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_007", table_key="batch-test")

    prompts = mock_generator.generate_batch.call_args.kwargs["prompts"]
    assert prompts == ["base\na", "base\na", "base\na"]


@pytest.mark.asyncio
async def test_batch_task_name_from_config_not_fields(
    batch_settings, mock_dingtalk, mock_generator
):
    """task_name 必须从配置读（不是从 fields 读）。"""
    # fields 里故意放一个不同的"任务名称"值
    mock_dingtalk.get_record.return_value = {
        "id": "recB_008",
        "fields": {
            "模特标准图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
            "任务名称": "SHOULD-NOT-BE-USED",
        },
    }
    mock_dingtalk.list_records.return_value = []
    mock_dingtalk.download_file.return_value = b"ref"

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_008", table_key="batch-test")

    # 验证传给 list_records 的 value == 配置里的 task_name，不是 fields 里的
    call_kwargs = mock_dingtalk.list_records.call_args.kwargs
    assert call_kwargs["value"] == "动作图-A"
    assert call_kwargs["field"] == "任务名称"


@pytest.mark.asyncio
async def test_batch_mode_false_calls_process_single(
    batch_settings, mock_dingtalk, mock_generator
):
    """batch_mode=false 时走 _process_single（旧流程）。"""
    # 默认 clothing 表是 batch_mode=False
    mock_dingtalk.get_record.return_value = {
        "id": "recB_009",
        "fields": {
            "提示词": "single prompt",
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "f.png", "size": 1, "type": "image/png", "url": "/u", "resourceId": "r"
    }
    mock_generator.generate.return_value = b"img"

    service = _make_batch_service(batch_settings, mock_dingtalk, mock_generator)
    await service.process(record_id="recB_009")  # default_table=clothing

    # 单图路径：generate_batch 不应被调
    mock_generator.generate_batch.assert_not_awaited()
    mock_generator.generate.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "成功"
    assert len(fields["生成图片"]) == 1


# ─────────── 配置校验 ────────────


def test_existing_config_loads_without_batch_mode(mock_settings):
    """现有 config（无 batch_mode）应正常加载，新字段全默认。"""
    for table in mock_settings.dingtalk.tables:
        assert table.batch_mode is False
        assert table.task_name is None
        assert table.prompt_table_sheet_id is None
        assert table.prompt_table is None


def test_batch_mode_missing_task_name_raises():
    """batch_mode=true 但缺 task_name → 启动失败。"""
    from config import Settings
    from utils.exceptions import ConfigError

    # 缺少 task_name
    bad_table = TableConfig(
        key="bad",
        base_id="tbl",
        sheet_id="sht",
        image_api_key_env="X",
        batch_mode=True,
        prompt_table_sheet_id="sht2",
        prompt_table=PromptTableConfig(),
    )
    bad_dt = DingtalkConfig(
        default_table="clothing",
        tables=[bad_table],
    )
    with pytest.raises(ConfigError, match="task_name"):
        Settings(
            dingtalk_app_key="k",
            dingtalk_app_secret="s",
            dingtalk_operator_id="o",
            dingtalk=bad_dt,
        )


def test_batch_mode_missing_prompt_table_sheet_id_raises():
    """batch_mode=true 但缺 prompt_table_sheet_id → 启动失败。"""
    from config import Settings
    from utils.exceptions import ConfigError

    bad_table = TableConfig(
        key="bad",
        base_id="tbl",
        sheet_id="sht",
        image_api_key_env="X",
        batch_mode=True,
        task_name="动作图-A",
        prompt_table=PromptTableConfig(),
    )
    bad_dt = DingtalkConfig(tables=[bad_table])
    with pytest.raises(ConfigError, match="prompt_table_sheet_id"):
        Settings(
            dingtalk_app_key="k",
            dingtalk_app_secret="s",
            dingtalk_operator_id="o",
            dingtalk=bad_dt,
        )


def test_batch_mode_complete_config_loads_ok():
    """batch_mode=true 配置完整 → 加载成功。"""
    from config import Settings

    good_table = TableConfig(
        key="good",
        base_id="tbl",
        sheet_id="sht",
        image_api_key_env="X",
        batch_mode=True,
        task_name="动作图-A",
        prompt_table_sheet_id="sht2",
        prompt_table=PromptTableConfig(),
    )
    good_dt = DingtalkConfig(tables=[good_table])
    s = Settings(
        dingtalk_app_key="k",
        dingtalk_app_secret="s",
        dingtalk_operator_id="o",
        dingtalk=good_dt,
    )
    assert s.dingtalk.tables[0].batch_mode is True
    assert s.dingtalk.tables[0].task_name == "动作图-A"
