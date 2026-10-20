#!/usr/bin/env python3
"""诊断脚本：对比三家 batch-action 表现状（提示词表动作图行数 + 生图表是否已加单选字段）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

from alibabacloud_dingtalk.notable_1_0 import models as notable_models
from alibabacloud_tea_util import models as util_models

from config import Settings
from dingtalk.client import DingTalkClient


def _fields(rec):
    fields = getattr(rec, "fields", None)
    if fields is None and isinstance(rec, dict):
        fields = rec.get("fields", {})
    return fields or {}


async def check(dingtalk, key):
    settings = Settings()
    tc = settings.get_table(key)
    token = await dingtalk._get_access_token()

    print("=" * 70)
    print(f"[{key}]")
    print("=" * 70)

    # 1. 提示词表：任务名称含"动作图"的行
    records = await dingtalk.list_records(
        base_id=tc.base_id, sheet_id=tc.prompt_table_sheet_id,
        field="任务名称", value=tc.task_name,
    )
    print(f"提示词表 任务名称='{tc.task_name}' 共 {len(records)} 条")
    for i, rec in enumerate(records):
        f = _fields(rec)
        print(f"  - 数量={f.get('生成数量')} 比例={f.get('生成比例')} "
              f"提示词前30字={repr(str(f.get('提示词', ''))[:30])}")

    # 顺带拉提示词表全部任务名称（用 notEmpty），看有哪些任务
    headers = notable_models.ListRecordsHeaders()
    headers.x_acs_dingtalk_access_token = token
    request = notable_models.ListRecordsRequest(
        operator_id=dingtalk.operator_id, max_results=50,
        filter=notable_models.ListRecordsRequestFilter(
            combination="and",
            conditions=[notable_models.ListRecordsRequestFilterConditions(
                field="任务名称", operator="notEmpty", value=[""],
            )],
        ),
    )
    resp = await dingtalk._client.list_records_with_options_async(
        base_id=tc.base_id, sheet_id_or_name=tc.prompt_table_sheet_id,
        request=request, headers=headers, runtime=util_models.RuntimeOptions(),
    )
    all_recs = resp.body.records if hasattr(resp, "body") else []
    names = [str(_fields(r).get("任务名称", "")) for r in all_recs]
    print(f"提示词表全部任务名称: {names}")

    # 2. 生图表字段元数据：看有没有"提示词"单选
    fh = notable_models.GetAllFieldsHeaders()
    fh.x_acs_dingtalk_access_token = token
    freq = notable_models.GetAllFieldsRequest(operator_id=dingtalk.operator_id)
    fresp = await dingtalk._client.get_all_fields_with_options_async(
        base_id=tc.base_id, sheet_id_or_name=tc.sheet_id,
        request=freq, headers=fh, runtime=util_models.RuntimeOptions(),
    )
    for f in fresp.body.to_map()["value"]:
        if f["name"] in ("提示词", "任务名称"):
            prop = f.get("property") or {}
            choices = [c["name"] for c in prop.get("choices", [])]
            print(f"生图表字段: {f['name']} ({f['type']}) 选项: {choices}")
    print()


async def main() -> int:
    settings = Settings()
    dingtalk = DingTalkClient(settings)
    for key in ("zhuozhi-batch-action", "huapu-batch-action", "ahmi-batch-action"):
        try:
            await check(dingtalk, key)
        except Exception as e:
            print(f"[{key}] 查询出错: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
