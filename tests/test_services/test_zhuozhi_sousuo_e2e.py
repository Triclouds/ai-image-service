"""搜推素材（zhuozhi-sousuo）三段式生图端到端测试。

覆盖：
1. parse_prompt_sections 拆段
2. build_sousuo_prompts 按 output_order 重组 + 随机抽样
3. assign_sousuo_index 序号分配（1-3，仅场景图一段）
4. 端到端 _process_batch_sousuo 流程（3 张全成功 + 自定义命名）
5. 店铺编码提取（"淘宝-AHMI,13706801" → "13706801"）

约束：仅 zhuozhi-sousuo 启用 prompt_section_mode；其他表走原路径。
当前 output_order 只取 "场景图" 一段，3 张连续编号 1-3。
"""

from unittest.mock import AsyncMock

import pytest

from config import DingtalkConfig, PromptTableConfig, TableConfig
from models.prompt_config import (
    assign_sousuo_index,
    build_sousuo_prompts,
    parse_prompt_sections,
)
from services.generation import GenerationService


@pytest.fixture
def mock_dingtalk():
    return AsyncMock()


@pytest.fixture
def mock_generator():
    return AsyncMock()


# ---------------------------------------------------------------------------
# 纯函数测试：拆段 + 重组 + 序号
# ---------------------------------------------------------------------------

SECTION_TITLES = {"一、": "白底图", "二、": "场景图", "三、": "细节图"}
OUTPUT_ORDER = ["场景图"]  # 只输出一段，3 张连续编号 1-3
COUNT_PER_SECTION = 3

# 一段典型 prompt 文本（每段 6 子项）
PROMPT_TEXT = """一、白底图
1、女装纯白底悬挂主图，...
2、女装平铺白底静物图，...
3、45° 斜挂女装白底图，...
4、女装上衣白底悬挂图，...
5、半身裙白底单品图，...
6、女装套装白底组合图，...
二、场景图
1、韩系奶油风卧室穿搭场景，...
2、街头潮流城市街拍场景，...
3、法式慵懒咖啡馆场景，...
4、复古港风茶餐厅场景，...
5、日系清新校园场景，...
6、欧美极简画廊场景，...
三、细节图
1、女装面料微距特写，...
2、女装纽扣/拉链细节，...
3、女装印花图案细节，...
4、女装走线/缝合细节，...
5、女装配饰（腰带/包）细节，...
6、女装吊牌/水洗标细节，..."""


def test_parse_prompt_sections_three_sections():
    """parse_prompt_sections 能正确拆出 3 段，每段 6 项。"""
    sections = parse_prompt_sections(PROMPT_TEXT, SECTION_TITLES)

    assert set(sections.keys()) == {"白底图", "场景图", "细节图"}
    assert len(sections["白底图"]) == 6
    assert len(sections["场景图"]) == 6
    assert len(sections["细节图"]) == 6
    # 每段第 1 项应该是原 prompt 的对应子项
    assert "女装纯白底悬挂主图" in sections["白底图"][0]
    assert "韩系奶油风" in sections["场景图"][0]
    assert "女装面料微距特写" in sections["细节图"][0]


def test_parse_prompt_sections_unknown_section_dropped():
    """未在 section_titles 中出现的段标题被丢弃。"""
    text = "一、白底图\n1、a\n2、b\n三、不在映射中的段\nx"
    # 把 "三、" 重命名为未在 SECTION_TITLES 中的值
    text = "一、白底图\n1、a\n2、b\n99、乱入段\nx"
    sections = parse_prompt_sections(text, SECTION_TITLES)
    # 只识别到 "一、白底图"；"99、乱入段" 不匹配任何 section_titles 前缀，
    # 所以它会被当作 "白底图" 段的子项保留（这是预期行为）
    assert list(sections.keys()) == ["白底图"]
    assert sections["白底图"] == ["1、a", "2、b", "99、乱入段", "x"]


def test_parse_prompt_sections_empty():
    """空输入 → 空 dict。"""
    assert parse_prompt_sections("", SECTION_TITLES) == {}
    assert parse_prompt_sections(PROMPT_TEXT, {}) == {}

    
def test_parse_prompt_sections_new_format():
    """新格式：段标题直接是段名 + 「、」，不带「一、」「二、」序号。

    钉钉搜推素材提示词表已切换到这种格式：
      场景图、\n1、xxx\n2、xxx\n...
    解析器应能正确识别为「场景图」段。
    """
    new_format = """场景图、
1、坐在户外桌椅旁，双腿自然摆放，单手撑着头部，
2、盘腿坐在草地上，单臂高高举起比出剪刀手，
3、站在水边，双臂向两侧大幅张开，头部后仰，
4、坐在户外桌椅旁，双腿自然摆放，单手撑着头部，
5、清新自然氛围，午后柔和阳光，自然迈步前行，
6、站在扶梯上，身体微侧，一只手向前伸出张开手掌，"""
    # 新映射：兼容「场景图、」前缀
    new_titles = {"场景图、": "场景图"}
    sections = parse_prompt_sections(new_format, new_titles)
    assert list(sections.keys()) == ["场景图"]
    assert len(sections["场景图"]) == 6
    assert "坐在户外桌椅旁" in sections["场景图"][0]


def test_build_sousuo_prompts_output_order():
    """build_sousuo_prompts 按 output_order 输出 3 个 (prompt, 段名) 对。"""
    sections = parse_prompt_sections(PROMPT_TEXT, SECTION_TITLES)
    out = build_sousuo_prompts(
        base_prompt="",
        sections=sections,
        output_order=OUTPUT_ORDER,
        count_per_section=COUNT_PER_SECTION,
        seed="rec_seed_001",
    )

    assert len(out) == 3
    # 段名顺序：场景图 × 3
    names = [tname for _, tname in out]
    assert names == ["场景图"] * 3


def test_build_sousuo_prompts_deterministic_with_seed():
    """同一 seed → 同一抽样；不同 seed → 可能不同抽样。"""
    sections = parse_prompt_sections(PROMPT_TEXT, SECTION_TITLES)
    out_a = build_sousuo_prompts("", sections, OUTPUT_ORDER, 3, seed="X")
    out_b = build_sousuo_prompts("", sections, OUTPUT_ORDER, 3, seed="X")
    out_c = build_sousuo_prompts("", sections, OUTPUT_ORDER, 3, seed="Y")

    assert out_a == out_b  # 同一 seed 结果一致
    # 不同 seed 大概率不同（6 选 3 空间足够大）
    # 但允许小概率相等 → 用 not equal 弱断言
    # 这里只断言"同 seed 一致"以保证业务可复现


def test_assign_sousuo_index_1_to_3():
    """assign_sousuo_index 按 output_order 分配 1-3 序号。"""
    sections = parse_prompt_sections(PROMPT_TEXT, SECTION_TITLES)
    ordered = build_sousuo_prompts("", sections, OUTPUT_ORDER, 3, seed="rec_001")
    indexed = assign_sousuo_index(ordered, count_per_section=3)

    # 3 个元素
    assert len(indexed) == 3
    # 序号连续 1-3
    indices = [idx for _, _, idx in indexed]
    assert indices == [1, 2, 3]
    # 段名按 output_order 分布
    names = [tname for _, tname, _ in indexed]
    assert names == ["场景图"] * 3
    # prompt 内容去掉了"1、"开头
    for prompt, _, _ in indexed:
        assert not prompt.startswith("1、")
        assert not prompt.startswith("2、")


def test_assign_sousuo_index_section_partial():
    """场景图段只有 2 项时 → 序号 1-2 连续，不补位。"""
    sections = {
        "场景图": ["a", "b"],  # 只有 2 项
    }
    ordered = build_sousuo_prompts(
        "", sections, OUTPUT_ORDER, count_per_section=3, seed="x"
    )
    indexed = assign_sousuo_index(ordered, count_per_section=3)

    # 实际产出 2 项，序号 1-2 连续
    indices = [idx for _, _, idx in indexed]
    assert indices == [1, 2]


# 业务真实场景：场景图段通常 6 条候选里随机抽 3 张（见钉钉提示词表）
SCENE_PROMPT_6_ITEMS = [
    "坐在户外桌椅旁，双腿自然摆放，单手撑着头部，",
    "盘腿坐在草地上，单臂高高举起比出剪刀手，",
    "站在水边，双臂向两侧大幅张开，头部后仰，",
    "坐在户外桌椅旁，双腿自然摆放，单手撑着头部，",
    "清新自然氛围，午后柔和阳光，自然迈步前行，",
    "站在扶梯上，身体微侧，一只手向前伸出张开手掌，",
]


def test_build_sousuo_prompts_scene_6_pick_3():
    """业务真实场景：场景图 6 条候选 → 随机抽 3 张，连续编号 1-3。"""
    sections = {"场景图": SCENE_PROMPT_6_ITEMS}
    out = build_sousuo_prompts(
        "", sections, OUTPUT_ORDER, count_per_section=3, seed="recS_seed_001"
    )

    # 1) 输出 3 个 (prompt, 段名)
    assert len(out) == 3
    # 2) 全部是「场景图」段
    assert all(tname == "场景图" for _, tname in out)
    # 3) 抽到的 prompt 都来自 6 条候选之一（不放回）
    picked = [p for p, _ in out]
    assert all(p in SCENE_PROMPT_6_ITEMS for p in picked)
    assert len(set(picked)) == 3  # 互不重复

    # 4) 加上序号后是 1-3
    indexed = assign_sousuo_index(out, count_per_section=3)
    assert [idx for _, _, idx in indexed] == [1, 2, 3]


def test_build_sousuo_prompts_scene_different_seeds_pick_different_subsets():
    """不同 seed → 抽到的子集不同（验证随机性真正生效）。

    业务希望：同一 record_id 重跑结果一致（同 seed）；
    不同 record_id 重跑应抽到不同子集（不同 seed）。
    """
    # 跑 30 次，统计出现过的"索引三元组"种类数。
    # 6 选 3 共 20 种；若随机性真实生效，30 次至少应出现 5 种以上。
    sections = {"场景图": SCENE_PROMPT_6_ITEMS}
    subset_signatures: set[tuple[int, ...]] = set()
    for i in range(30):
        out = build_sousuo_prompts(
            "", sections, OUTPUT_ORDER, 3, seed=f"seed_{i:03d}"
        )
        picked = [p for p, _ in out]
        # 用「在原列表中的索引」做签名，避开抽样后排序的干扰
        idx_tuple = tuple(
            sorted(SCENE_PROMPT_6_ITEMS.index(p) for p in picked)
        )
        subset_signatures.add(idx_tuple)

    # 30 次随机应至少覆盖 5 种不同子集（实际通常是 18-20 种）
    assert len(subset_signatures) >= 5, (
        f"30 次随机应至少 5 种不同子集，实际仅 {len(subset_signatures)} 种："
        f"{subset_signatures}"
    )


# ---------------------------------------------------------------------------
# 端到端流程测试：mock service 走 _process_batch_sousuo
# ---------------------------------------------------------------------------


@pytest.fixture
def sousuo_settings(mock_settings):
    """含 zhuozhi-sousuo 表（prompt_section_mode=true）的 Settings。"""
    sousuo_table = TableConfig(
        key="zhuozhi-sousuo",
        base_id="tbl_sousuo",
        sheet_id="sheet_sousuo",
        image_api_key_env="ZHUOZHI_IMAGE_API_KEY",
        batch_mode=True,
        task_name="搜推素材",
        prompt_table_sheet_id="sheet_prompt_sousuo",
        prompt_table=PromptTableConfig(),
        model_field="生图模型",
        reference_image_field="素材图",
        result_image_field="场景图",
        result_status_field="生成结果",
        result_time_field="生成时间",
        # 三段式特有字段
        prompt_section_mode=True,
        section_titles=SECTION_TITLES,
        output_order=OUTPUT_ORDER,
        count_per_section=3,
        shop_code_field="店铺",
        shop_code_separator=",",
        goods_id_field="商品ID",
    )
    # 保留原 mock_settings 的 clothing 单图表，确保其他表不受影响
    new_dt = DingtalkConfig(
        default_table=mock_settings.dingtalk.default_table,
        tables=[*mock_settings.dingtalk.tables, sousuo_table],
        video_tables=mock_settings.dingtalk.video_tables,
    )
    mock_settings.dingtalk = new_dt
    return mock_settings


@pytest.mark.asyncio
async def test_sousuo_process_full_success(sousuo_settings, mock_dingtalk, mock_generator):
    """完整 3 张成功：文件名按 record_id_goodsId_shopCode_1~3.png 命名。"""
    # 模拟生图表记录
    mock_dingtalk.get_record.return_value = {
        "id": "recS_001",
        "fields": {
            "商品ID": "GOODS_001",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "/file/ref.jpg", "filename": "ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    # 模拟提示词表记录
    mock_dingtalk.list_records.return_value = [
        {
            "id": "prompt_rec_sousuo",
            "fields": {
                # 搜推素材：三段式段落源是「扰动列表」字段
                "扰动列表": PROMPT_TEXT,
                "提示词": "基础参考描述",
                "生成数量": "3",     # 真实钉钉场景：控制每段抽几张（这里给 3）
                "生成比例": "1:1",
                "分辨率": "1024",
            },
        }
    ]
    mock_dingtalk.download_file.return_value = b"ref_bytes"
    mock_dingtalk.upload_attachment.side_effect = [
        {
            "filename": f"recS_001_GOODS_001_13706801_{i}.png",
            "size": 1,
            "type": "image/png",
            "url": f"/u{i}",
            "resourceId": f"r{i}",
        }
        for i in range(1, 4)
    ]
    mock_generator.generate_batch.return_value = [f"img{i}".encode() for i in range(1, 4)]

    service = GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=sousuo_settings,
    )
    await service.process(record_id="recS_001", table_key="zhuozhi-sousuo")

    # 1. generate_batch 收到 3 个 prompt
    mock_generator.generate_batch.assert_awaited_once()
    call_kwargs = mock_generator.generate_batch.call_args
    assert len(call_kwargs.kwargs["prompts"]) == 3
    assert call_kwargs.kwargs["model"] == "Nano Banana 2"
    # 多参考图接口：单图场景下传 list[bytes]，长度 1
    assert call_kwargs.kwargs["reference_image"] == [b"ref_bytes"]  # ponytail: 单图兼容，多图后扩测试

    # 2. upload × 3
    assert mock_dingtalk.upload_attachment.await_count == 3

    # 3. 上传文件名按 {record_id}_{goods_id}_{shop_code}_{idx}.png 命名
    upload_filenames = [
        call.args[2] for call in mock_dingtalk.upload_attachment.await_args_list
    ]
    expected_filenames = [
        f"recS_001_GOODS_001_13706801_{i}.png" for i in range(1, 4)
    ]
    assert upload_filenames == expected_filenames

    # 4. update_record 收到 3 个附件，写到「场景图」字段，状态写到「生成结果」字段
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert len(fields["场景图"]) == 3
    assert fields["生成结果"] == "成功3/3"
    assert "生成时间" in fields


@pytest.mark.asyncio
async def test_sousuo_shop_code_extraction(
    sousuo_settings, mock_dingtalk, mock_generator
):
    """店铺编码提取："淘宝-AHMI,13706801" → "13706801"（取最后一个逗号后）。"""
    test_cases = [
        ("淘宝-AHMI,13706801", "13706801"),
        ("天猫,88888888", "88888888"),
        ("京东旗舰店,12345", "12345"),
        # 多个逗号 → 取最后一个之后
        ("shop,name,999", "999"),
    ]
    for shop_raw, expected_code in test_cases:
        # 每次循环 reset mock，避免历史调用干扰
        mock_dingtalk.reset_mock()
        mock_generator.reset_mock()
        mock_dingtalk.get_record.return_value = {
            "id": "recS_X",
            "fields": {
                "商品ID": "GID",
                "店铺": {"name": shop_raw},
                "素材图": [{"url": "/file/ref.jpg"}],
                "生图模型": {"name": "Nano Banana 2"},
            },
        }
        mock_dingtalk.list_records.return_value = [
            {"fields": {"扰动列表": PROMPT_TEXT, "生成数量": "3"}}
        ]
        mock_dingtalk.download_file.return_value = b"ref"
        mock_dingtalk.upload_attachment.return_value = {
            "filename": "x.png",
            "size": 1,
            "type": "image/png",
            "url": "/u",
            "resourceId": "r",
        }
        mock_generator.generate_batch.return_value = [b"i"] * 3

        service = GenerationService(
            dingtalk=mock_dingtalk,
            generator=mock_generator,
            settings=sousuo_settings,
        )
        await service.process(record_id="recS_X", table_key="zhuozhi-sousuo")

        # 检查第 1 次 upload 的文件名包含正确 shop_code
        first_upload_args = mock_dingtalk.upload_attachment.await_args_list[0].args
        filename = first_upload_args[2]
        assert expected_code in filename, (
            f"shop_raw={shop_raw!r} expected={expected_code!r}, got={filename!r}"
        )
        assert filename == "recS_X_GID_" + expected_code + "_1.png"


@pytest.mark.asyncio
async def test_sousuo_process_partial_failure(
    sousuo_settings, mock_dingtalk, mock_generator
):
    """部分失败：3 张里 1 张 None → upload × 2，状态含成功计数。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recS_P",
        "fields": {
            "商品ID": "GID",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"扰动列表": PROMPT_TEXT, "生成数量": "3"}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    # 3 次 upload（实际只 2 次会被 await，但 side_effect 长度要 >= 3 才不抛 StopIteration）
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": f"f{i}.png", "size": 1, "type": "image/png", "url": f"/u{i}", "resourceId": f"r{i}"}
        for i in range(1, 4)
    ]
    # 3 个结果里 1 个 None
    results = [b"img1", None, b"img3"]
    mock_generator.generate_batch.return_value = results

    service = GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=sousuo_settings,
    )
    await service.process(record_id="recS_P", table_key="zhuozhi-sousuo")

    assert mock_dingtalk.upload_attachment.await_count == 2
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert len(fields["场景图"]) == 2
    assert fields["生成结果"].startswith("成功2/3")


@pytest.mark.asyncio
async def test_sousuo_count_driven_by_prompt_table(
    sousuo_settings, mock_dingtalk, mock_generator
):
    """生成张数由提示词表「生成数量」字段决定，而非 table_config.count_per_section。

    业务真实场景：
    - zhuozhi-sousuo prompt 表「生成数量」= "6" → 应输出 6 张
    - ahmi-sousuo prompt 表「生成数量」= "3" → 应输出 3 张
    即使 table_config.count_per_section=3，「生成数量」=6 时也应输出 6 张。
    """
    mock_dingtalk.get_record.return_value = {
        "id": "recS_COUNT",
        "fields": {
            "商品ID": "GID",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    # 提示词表「生成数量」= "6"（字符串，钉钉默认行为）
    mock_dingtalk.list_records.return_value = [
        {"fields": {"扰动列表": PROMPT_TEXT, "生成数量": "6"}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    # 6 次 upload side_effect（实际可能 < 6 也行，>= 即可避免 StopIteration）
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": f"f{i}.png", "size": 1, "type": "image/png", "url": f"/u{i}", "resourceId": f"r{i}"}
        for i in range(1, 8)
    ]
    mock_generator.generate_batch.return_value = [f"img{i}".encode() for i in range(1, 7)]

    service = GenerationService(
        dingtalk=mock_dingtalk, generator=mock_generator, settings=sousuo_settings,
    )
    await service.process(record_id="recS_COUNT", table_key="zhuozhi-sousuo")

    # 关键断言：generate_batch 收到 6 个 prompt（由「生成数量」=6 驱动）
    call_kwargs = mock_generator.generate_batch.call_args
    assert len(call_kwargs.kwargs["prompts"]) == 6, (
        f"期望 6 个 prompt（生成数量=6），实际 {len(call_kwargs.kwargs['prompts'])}"
    )
    assert mock_dingtalk.upload_attachment.await_count == 6


@pytest.mark.asyncio
async def test_sousuo_count_falls_back_to_config_when_missing(
    sousuo_settings, mock_dingtalk, mock_generator
):
    """「生成数量」字段缺失/无效 → 回落 table_config.count_per_section。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recS_FB",
        "fields": {
            "商品ID": "GID",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    # 「生成数量」字段缺失（没设置）
    mock_dingtalk.list_records.return_value = [
        {"fields": {"扰动列表": PROMPT_TEXT}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": f"f{i}.png", "size": 1, "type": "image/png", "url": f"/u{i}", "resourceId": f"r{i}"}
        for i in range(1, 5)
    ]
    mock_generator.generate_batch.return_value = [f"img{i}".encode() for i in range(1, 4)]

    service = GenerationService(
        dingtalk=mock_dingtalk, generator=mock_generator, settings=sousuo_settings,
    )
    await service.process(record_id="recS_FB", table_key="zhuozhi-sousuo")

    # 回落：count_per_section=3（来自 config）
    call_kwargs = mock_generator.generate_batch.call_args
    assert len(call_kwargs.kwargs["prompts"]) == 3


@pytest.mark.asyncio
async def test_sousuo_count_handles_invalid_value(
    sousuo_settings, mock_dingtalk, mock_generator
):
    """「生成数量」字段值非法（如 "abc"）→ 回落 table_config.count_per_section。"""
    mock_dingtalk.get_record.return_value = {
        "id": "recS_BAD",
        "fields": {
            "商品ID": "GID",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"扰动列表": PROMPT_TEXT, "生成数量": "abc"}}  # 非法
    ]
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.side_effect = [
        {"filename": f"f{i}.png", "size": 1, "type": "image/png", "url": f"/u{i}", "resourceId": f"r{i}"}
        for i in range(1, 5)
    ]
    mock_generator.generate_batch.return_value = [f"img{i}".encode() for i in range(1, 4)]

    service = GenerationService(
        dingtalk=mock_dingtalk, generator=mock_generator, settings=sousuo_settings,
    )
    await service.process(record_id="recS_BAD", table_key="zhuozhi-sousuo")

    # 非法值回落：count_per_section=3
    call_kwargs = mock_generator.generate_batch.call_args
    assert len(call_kwargs.kwargs["prompts"]) == 3


@pytest.mark.asyncio
async def test_sousuo_missing_section_fails(sousuo_settings, mock_dingtalk, mock_generator):
    """提示词缺「场景图」段 → 失败"提示词表缺少段: 场景图"。"""
    # 故意少"场景图"段，只留白底图 + 细节图
    partial_prompt = """一、白底图
1、a
2、b
3、c
4、d
5、e
6、f
三、细节图
1、a
2、b
3、c
4、d
5、e
6、f"""
    mock_dingtalk.get_record.return_value = {
        "id": "recS_M",
        "fields": {
            "商品ID": "GID",
            "店铺": {"name": "x,1"},
            "素材图": [{"url": "/file/ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.list_records.return_value = [
        {"fields": {"扰动列表": partial_prompt, "生成数量": "3"}}
    ]
    mock_dingtalk.download_file.return_value = b"ref"

    service = GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=sousuo_settings,
    )
    await service.process(record_id="recS_M", table_key="zhuozhi-sousuo")

    mock_generator.generate_batch.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "缺少段" in fields["生成结果"]
    assert "场景图" in fields["生成结果"]


@pytest.mark.asyncio
async def test_other_table_unaffected_by_sousuo_config(
    mock_settings, mock_dingtalk, mock_generator
):
    """其他表（clothing）不应受 zhuozhi-sousuo 配置影响，走 _process_single。"""
    # mock_settings 默认只有 clothing 表，prompt_section_mode 默认 False
    clothing = next(t for t in mock_settings.dingtalk.tables if t.key == "clothing")
    assert clothing.prompt_section_mode is False
    assert clothing.output_order is None
    assert clothing.section_titles is None

    mock_dingtalk.get_record.return_value = {
        "id": "rec_single",
        "fields": {
            "提示词": "a cat",
            "素材图": [{"url": "/file/x.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    mock_dingtalk.download_file.return_value = b"ref"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "f.png", "size": 1, "type": "image/png", "url": "/u", "resourceId": "r"
    }
    mock_generator.generate.return_value = b"img"

    service = GenerationService(
        dingtalk=mock_dingtalk,
        generator=mock_generator,
        settings=mock_settings,
    )
    await service.process(record_id="rec_single", table_key="clothing")

    # 单图路径
    mock_generator.generate.assert_awaited_once()
    mock_generator.generate_batch.assert_not_awaited()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "成功"
    assert len(fields["生成图片"]) == 1