"""GenerationService 单元测试。"""

from unittest.mock import AsyncMock

import httpx
import pytest

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
    )
