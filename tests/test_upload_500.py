"""测试 ahmi-batch-action 小文件上传（限流恢复后）。

用法: python tests/test_upload_500.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import Settings
from dingtalk.client import DingTalkClient


async def main():
    settings = Settings()
    tc = settings.get_table("ahmi-batch-action")
    client = DingTalkClient(settings)

    for size in [100, 500, 1024, 2048, 5120]:
        kb = size
        img = os.urandom(kb * 1024)
        try:
            result = await client.upload_attachment(tc, img, f"test_{kb}kb.png")
            print(f"  {kb}KB OK")
        except Exception as e:
            msg = str(e)
            has_500 = "unknownError" in msg
            print(f"  {kb}KB {'FAIL(500)' if has_500 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
