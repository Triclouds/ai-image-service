#!/usr/bin/env python3
"""读 record fIwzezxeVT 的当前附件字段，确认 9 张图已写入。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

from config import Settings
from dingtalk.client import DingTalkClient


async def main() -> int:
    settings = Settings()
    table_config = settings.get_table("zhuozhi-sousuo")
    dingtalk = DingTalkClient(settings)

    print(f"读 record_id = fIwzezxeVT")
    record = await dingtalk.get_record(table_config, "fIwzezxeVT")
    fields = record.get("fields", {})

    print(f"\n=== 字段 ===")
    for k, v in fields.items():
        v_str = str(v)[:200]
        print(f"  {k}: {v_str}")

    # 解析附件字段
    attachments = fields.get(table_config.result_image_field) or []
    print(f"\n=== 附件（场景图字段，{len(attachments)} 张）===")
    for i, att in enumerate(attachments, 1):
        fname = att.get("filename", "?")
        size = att.get("size", 0)
        url = att.get("url", "?")
        print(f"  {i}. {fname}  ({size:,} bytes)  url={url}")

    print(f"\n去钉钉表看实际效果:")
    print(f"  https://alidocs.dingtalk.com/i/notable/?baseId={table_config.base_id}&sheetId={table_config.sheet_id}&rowId=fIwzezxeVT")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))