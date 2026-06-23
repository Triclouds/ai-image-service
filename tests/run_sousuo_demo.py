#!/usr/bin/env python3
"""搜推素材三段式 demo：端到端可视化脚本。

不走真实钉钉 / AI SDK，只用 PIL 生成 9 张图（每张带段名 + 序号水印），
落地到 tests/output/sousuo_demo/，方便肉眼验证：
- 拆段正确（白底图 / 场景图 / 细节图 各 6 → 抽 3）
- output_order 顺序（细节 → 场景 → 白底）
- 序号 1-9 映射（细节 1-3 / 场景 4-6 / 白底 7-9）
- 文件名 {record_id}_{goods_id}_{shop_code}_{idx}.png
- 店铺编码提取（"淘宝-AHMI,13706801" → "13706801"）
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# 让脚本能 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image, ImageDraw, ImageFont

from config import DingtalkConfig, PromptTableConfig, Settings, TableConfig
from services.generation import GenerationService


SECTION_TITLES = {"一、": "白底图", "二、": "场景图", "三、": "细节图"}
OUTPUT_ORDER = ["细节图", "场景图", "白底图"]

PROMPT_TEXT = """一、白底图
1、女装纯白底悬挂主图，简洁大方
2、女装平铺白底静物图，平整美观
3、45° 斜挂女装白底图，立体感强
4、女装上衣白底悬挂图，版型清晰
5、半身裙白底单品图，剪裁精致
6、女装套装白底组合图，整体协调
二、场景图
1、韩系奶油风卧室穿搭场景
2、街头潮流城市街拍场景
3、法式慵懒咖啡馆场景
4、复古港风茶餐厅场景
5、日系清新校园场景
6、欧美极简画廊场景
三、细节图
1、女装面料微距特写
2、女装纽扣/拉链细节
3、女装印花图案细节
4、女装走线/缝合细节
5、女装配饰（腰带/包）细节
6、女装吊牌/水洗标细节"""


def _make_dummy_image(text: str, idx: int, tname: str, out_path: Path) -> bytes:
    """生成带文字水印的占位 PNG，返回 bytes 供 mock 上传使用。"""
    img = Image.new("RGB", (640, 480), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)

    # 优先用系统中文字体，缺失则退化到默认
    font_path = None
    for cand in [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]:
        if Path(cand).exists():
            font_path = cand
            break
    font_big = ImageFont.truetype(font_path, 56) if font_path else ImageFont.load_default()
    font_small = ImageFont.truetype(font_path, 32) if font_path else ImageFont.load_default()

    # 标题：序号 + 段名
    title = f"#{idx} {tname}"
    draw.text((40, 60), title, fill=(30, 30, 30), font=font_big)
    # 内容：截取 prompt 摘要
    summary = text[:24].replace("\n", " ")
    draw.text((40, 160), summary, fill=(80, 80, 80), font=font_small)
    # 角标：缩略示意
    draw.rectangle([(40, 240), (600, 440)], outline=(180, 180, 180), width=3)
    draw.text((280, 320), "[mock image]", fill=(200, 200, 200), font=font_small)

    img.save(out_path, format="PNG")
    return out_path.read_bytes()


def _build_settings() -> Settings:
    """构造一份 demo 用的 Settings（仅含 zhuozhi-sousuo 表 + 一个 batch-test 兜底）。"""
    return Settings(
        dingtalk_app_key="demo_key",
        dingtalk_app_secret="demo_secret",
        dingtalk_operator_id="demo_operator",
        api_key="demo_api",
        dingtalk=DingtalkConfig(
            default_table="zhuozhi-sousuo",
            tables=[
                TableConfig(
                    key="zhuozhi-sousuo",
                    base_id="tbl_demo",
                    sheet_id="sheet_demo",
                    image_api_key_env="ZHUOZHI_IMAGE_API_KEY",
                    batch_mode=True,
                    task_name="搜推素材",
                    prompt_table_sheet_id="sheet_prompt",
                    prompt_table=PromptTableConfig(),
                    model_field="生图模型",
                    reference_image_field="素材图",
                    result_image_field="生成结果",
                    result_status_field="状态",
                    result_time_field="生成时间",
                    prompt_section_mode=True,
                    section_titles=SECTION_TITLES,
                    output_order=OUTPUT_ORDER,
                    count_per_section=3,
                    shop_code_field="店铺",
                    shop_code_separator=",",
                    goods_id_field="商品ID",
                ),
            ],
        ),
    )


async def run_demo() -> None:
    settings = _build_settings()

    # 输出目录
    out_dir = Path(__file__).resolve().parent / "output" / "sousuo_demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    # mock 钉钉
    dingtalk = AsyncMock()
    dingtalk.get_record.return_value = {
        "id": "REC_DEMO_001",
        "fields": {
            "商品ID": "GOODS_DEMO_001",
            "店铺": {"name": "淘宝-AHMI,13706801"},
            "素材图": [{"url": "mock://ref.jpg", "filename": "ref.jpg"}],
            "生图模型": {"name": "Nano Banana 2"},
        },
    }
    dingtalk.list_records.return_value = [
        {
            "id": "PROMPT_REC",
            "fields": {
                "提示词": PROMPT_TEXT,
                "生成比例": "1:1",
                "分辨率": "1024",
            },
        }
    ]
    dingtalk.download_file.return_value = b"ref_bytes_dummy"

    # mock 生成器：边调边生成（用 record_id 当 seed 复用真实业务确定性）
    from models.prompt_config import (
        assign_sousuo_index,
        build_sousuo_prompts,
        parse_prompt_sections,
    )

    sections = parse_prompt_sections(PROMPT_TEXT, SECTION_TITLES)
    ordered = build_sousuo_prompts(
        "", sections, OUTPUT_ORDER, count_per_section=3, seed="REC_DEMO_001"
    )
    indexed = assign_sousuo_index(ordered, count_per_section=3)

    print("=" * 70)
    print("搜推素材三段式 demo 配置")
    print("=" * 70)
    print(f"record_id        = REC_DEMO_001")
    print(f"goods_id         = GOODS_DEMO_001")
    print(f"shop_raw         = 淘宝-AHMI,13706801")
    print(f"shop_code        = 13706801  (取最后一个 ',' 之后)")
    print(f"output_order     = {OUTPUT_ORDER}")
    print(f"count_per_section= 3")
    print()
    print("本次生成的 9 张：")
    print("-" * 70)
    for prompt, tname, idx in indexed:
        print(f"  #{idx:>2} {tname:<6}  prompt={prompt[:30]}...")

    # 用真实生成器的 generate_batch 调用时机，把每张图落到磁盘
    async def fake_generate_batch(*, model, prompts, reference_image, **kwargs):
        # 按 indexed 顺序映射 prompt → idx + tname
        results = []
        for prompt, tname, idx in indexed:
            fname = f"REC_DEMO_001_GOODS_DEMO_001_13706801_{idx}.png"
            fpath = out_dir / fname
            img_bytes = _make_dummy_image(prompt, idx, tname, fpath)
            results.append(img_bytes)
        return results

    async def fake_upload(table_config, img_bytes, filename):
        return {
            "filename": filename,
            "size": len(img_bytes),
            "type": "image/png",
            "url": f"mock://uploaded/{filename}",
            "resourceId": f"res_{filename}",
        }

    dingtalk.upload_attachment.side_effect = fake_upload

    generator = AsyncMock()
    generator.generate_batch.side_effect = fake_generate_batch

    service = GenerationService(dingtalk=dingtalk, generator=generator, settings=settings)
    await service.process(record_id="REC_DEMO_001", table_key="zhuozhi-sousuo")

    print()
    print("=" * 70)
    print("落地文件：")
    print("=" * 70)
    for p in sorted(out_dir.glob("*.png")):
        print(f"  {p.name}  ({p.stat().st_size} bytes)")

    print()
    print(f"输出目录: {out_dir}")
    print("请打开图片确认：")
    print("  - 序号水印 = 文件名后缀 = 1..9")
    print("  - 段名水印 = 细节图/场景图/白底图")
    print("  - 顺序：细节图 × 3 → 场景图 × 3 → 白底图 × 3")


if __name__ == "__main__":
    asyncio.run(run_demo())