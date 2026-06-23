#!/usr/bin/env python3
"""诊断脚本：拉提示词表 42YsOaT 看搜推素材任务的提示词原文。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

from config import Settings
from dingtalk.client import DingTalkClient


def _get_field(rec, key):
    fields = getattr(rec, "fields", None) or (
        rec.get("fields", {}) if isinstance(rec, dict) else {}
    )
    if not isinstance(fields, dict):
        return None
    return fields.get(key)


async def main() -> int:
    settings = Settings()
    table_config = settings.get_table("zhuozhi-sousuo")
    dingtalk = DingTalkClient(settings)

    print("=" * 70)
    print(f"提示词表诊断")
    print(f"base_id      = {table_config.base_id}")
    print(f"sheet_id     = {table_config.prompt_table_sheet_id}")
    print(f"task_name    = {table_config.task_name}")
    print("=" * 70)

    records = await dingtalk.list_records(
        base_id=table_config.base_id,
        sheet_id=table_config.prompt_table_sheet_id,
        field="任务名称",
        value=table_config.task_name,
    )

    print(f"拉到 {len(records)} 条任务名称='{table_config.task_name}' 的记录")
    if not records:
        print("提示：没有任务名称匹配的记录，看看是不是 task_name 拼错了？")
        # 兜底：拉前 20 条看有哪些任务名称
        print("\n=== 兜底扫描：拉前 20 条提示词表 ===")
        for cand in ["动作图", "白底图", "场景图", "细节图", "商品图"]:
            try:
                recs2 = await dingtalk.list_records(
                    base_id=table_config.base_id,
                    sheet_id=table_config.prompt_table_sheet_id,
                    field="任务名称",
                    value=cand,
                )
                print(f"  '{cand}': {len(recs2)} 条")
            except Exception as e:
                print(f"  '{cand}': 错误 {e}")
        return 1

    for i, rec in enumerate(records):
        rec_id = getattr(rec, "record_id", None) or getattr(rec, "id", None)
        fields_dict = _get_field(rec, "") or {}
        if not fields_dict:
            for attr in ("fields",):
                v = getattr(rec, attr, None)
                if v:
                    fields_dict = v if isinstance(v, dict) else {k: getattr(v, k, None) for k in dir(v) if not k.startswith("_")}
                    break

        print(f"\n--- 记录 {i+1}: id={rec_id} ---")
        for k, v in fields_dict.items():
            v_repr = repr(v)[:200]
            print(f"  {k}: {v_repr}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))