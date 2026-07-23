#!/usr/bin/env python3
"""多参考图真实生图测试（绕过钉钉，直接打 AI 引擎）。

只验证本次改动：AIGenerator.generate 接收 list[bytes] 多图输入。
读本地 2~N 张图 → 调真实模型图生图 → 存本地 PNG。不碰钉钉表格。

只需要一个环境变量：目标 table 的图片 API Key（默认 ZHUOZHI_IMAGE_API_KEY），
放进 .env 或 configs/.env 即可。

用法：
  python tests/run_multi_image_real.py a.png b.png c.png \
      --prompt "An office group photo of these people, making funny faces." \
      --model "Nano Banana 2" --out office.png

  # 换 GPT 分支验证多图：
  python tests/run_multi_image_real.py a.png b.png --model "GPT Image 2"

  --table 决定用哪个 image_api_key_env（默认 zhuozhi-base → ZHUOZHI_IMAGE_API_KEY）。
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main() -> int:
    parser = argparse.ArgumentParser(description="多参考图真实生图测试")
    parser.add_argument("images", nargs="+", help="本地参考图路径（1~N 张）")
    parser.add_argument(
        "--prompt",
        default="An office group photo of these people, they are making funny faces.",
    )
    parser.add_argument("--model", default="Nano Banana 2", help="config.toml 里的模型名")
    parser.add_argument("--table", default="zhuozhi-base", help="取 image_api_key_env 用")
    parser.add_argument("--aspect", default=None, help="比例，如 5:4（仅 Nano 生效）")
    parser.add_argument("--resolution", default=None, help="分辨率档位，如 2K")
    parser.add_argument("--out", default="multi_image_out.png")
    args = parser.parse_args()

    # 读图 + 存在性校验
    img_bytes: list[bytes] = []
    for p in args.images:
        fp = Path(p)
        if not fp.is_file():
            print(f"[FAIL] 找不到图片: {p}")
            return 1
        img_bytes.append(fp.read_bytes())
    print(f"[1/3] 读入 {len(img_bytes)} 张参考图: {', '.join(args.images)}")

    from config import Settings  # 延后导入：import 时会 load_dotenv
    from generator import AIGenerator

    settings = Settings()
    table_config = settings.get_table(args.table)

    key_env = table_config.image_api_key_env
    if not os.environ.get(key_env):
        print(f"[FAIL] 缺少环境变量 {key_env}（table={args.table} 的图片 API Key）")
        print(f"       请写入 configs/.env： {key_env}=<你的值>")
        return 1

    model_cfg = settings.get_model(args.model)
    print(
        f"[2/3] 调真实生图: model={args.model} provider={model_cfg.provider} "
        f"images={len(img_bytes)} aspect={args.aspect or '-'} res={args.resolution or '-'}"
    )

    generator = AIGenerator(settings)
    try:
        result = await generator.generate(
            model=args.model,
            prompt=args.prompt,
            reference_image=img_bytes,  # 多图：list[bytes]
            table_config=table_config,
            aspect_ratio=args.aspect,
            resolution=args.resolution,
        )
    except Exception as e:
        print(f"[FAIL] 生图异常: {type(e).__name__}: {e}")
        return 1
    finally:
        await generator.close()

    Path(args.out).write_bytes(result)
    print(f"[3/3] [OK] 生成 {len(result)} 字节 → {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
