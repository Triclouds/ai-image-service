"""upload_attachment 集成测试。

使用真实 .env / config.toml 配置，手动指定表名和记录 ID。
运行前需修改下方 TODO 标记的值。

用法:
    cd 项目根目录
    python tests/test_upload_integration.py
"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目根目录在 CWD 且 .env / config.toml 可被找到
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")

from config import Settings
from dingtalk.client import DingTalkClient


# ============================================================
# TODO: 运行前修改以下两个值
# ============================================================
# TABLE_KEY = "ahmi-base"       # config.toml 中的表格 key
# RECORD_ID = "0igoDwewc5"                 # 要测试上传的记录 ID
# TABLE_KEY = "huapu-base"       # config.toml 中的表格 key
# RECORD_ID = "7Gtfo83ZWU"                 # 要测试上传的记录 ID
TABLE_KEY = "zhuozhi-base"       # config.toml 中的表格 key
RECORD_ID = "RECORD_ID_HERE"                 # 要测试上传的记录 ID
# ============================================================


def _make_test_png() -> bytes:
    """生成一个最小的 1x1 蓝色 PNG 图片，用于测试上传。"""
    import struct
    import zlib

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit RGB
    raw = zlib.compress(b"\x00" + b"\x00\x00\xff")         # filter byte + BGR blue
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


async def main():
    if not RECORD_ID:
        print("错误: 请先修改脚本中的 RECORD_ID")
        sys.exit(1)

    settings = Settings()
    client = DingTalkClient(settings)
    table_config = settings.get_table(TABLE_KEY)

    print(f"表名: {table_config.key}")
    print(f"base_id: {table_config.base_id}")
    print(f"record_id: {RECORD_ID}")
    print(f"operator_id: {settings.dingtalk_operator_id}")

    test_image = _make_test_png()
    filename = f"test_upload_{RECORD_ID}.png"

    print(f"\n上传测试图片 ({len(test_image)} bytes) ...")
    try:
        result = await client.upload_attachment(table_config, test_image, filename)
    except Exception as e:
        print(f"\n上传失败: {e}")
        sys.exit(1)

    print("\n上传成功!")
    print(f"  filename:   {result['filename']}")
    print(f"  size:       {result['size']}")
    print(f"  type:       {result['type']}")
    print(f"  url:        {result['url']}")
    print(f"  resourceId: {result['resourceId']}")


if __name__ == "__main__":
    asyncio.run(main())
