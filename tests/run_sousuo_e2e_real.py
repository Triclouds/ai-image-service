#!/usr/bin/env python3
"""搜推素材真实端到端测试。

程序自动从 zhuozhi-sousuo 表找一条素材图已填的记录，跑完整流程：
  1. get_record 拉生图表数据
  2. 拆段 + 随机抽样 3 个 prompt（当前只配"场景图"一段）
  3. generate_batch 调 AI 生成 3 张
  4. upload_attachment × 3 到 zhuozhi-sousuo 表"场景图"字段
  5. update_record 写"成功 3/3"到"生成结果"字段 + 生成时间
完成后去钉钉表那条记录看结果。

凭证加载顺序（与主应用一致）：
  1. .env
  2. configs/.env
任一文件存在即可。建议把 DINGTALK_APP_KEY/SECRET/OPERATOR_ID、ZHUOZHI_IMAGE_API_KEY
放进 configs/.env，避免污染仓库。

record_id 自动查找策略（按优先级尝试）：
  A. 状态 = "待生成"（如果该字段是 singleSelect 且有该枚举值）
  B. 状态 = "生成中"
  C. 状态 不等于 "成功"/"已完成"（拉一批后本地再过滤）
  D. 兜底：拉前 50 条，本地过滤"素材图"非空的，取第一条
"""

import asyncio
import os
import sys
from pathlib import Path

# 让脚本能 import src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

# 先加载 .env，再加载 configs/.env（已有 env 不覆盖）
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)


def _check_credentials() -> None:
    """启动前快速 sanity check，避免跑到一半才发现缺 Key。"""
    required = [
        "DINGTALK_APP_KEY",
        "DINGTALK_APP_SECRET",
        "DINGTALK_OPERATOR_ID",
        "ZHUOZHI_IMAGE_API_KEY",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"[FAIL] 缺少环境变量: {', '.join(missing)}")
        print("请把以下行写入 configs/.env：")
        for k in missing:
            print(f"  {k}=<你的值>")
        sys.exit(1)


async def _pick_record_id(dingtalk, table_config) -> str | None:
    """从 zhuozhi-sousuo 表自动找一条素材图非空的记录。

    策略：
      1) 尝试按 result_status_field=状态 过滤 "待生成"（最常见）
      2) 尝试 "生成中"
      3) 拉一批 records，本地过滤"素材图"非空
    """
    base_id = table_config.base_id
    sheet_id = table_config.sheet_id
    ref_field = table_config.reference_image_field

    def _get_field(rec, key):
        """兼容 SDK 对象和 dict 两种 records 形态。"""
        fields = getattr(rec, "fields", None) or (
            rec.get("fields", {}) if isinstance(rec, dict) else {}
        )
        if not isinstance(fields, dict):
            return None
        return fields.get(key)

    def _get_id(rec):
        if isinstance(rec, dict):
            return rec.get("id")
        return getattr(rec, "record_id", None) or getattr(rec, "id", None)

    # 策略 1 & 2：用状态字段筛选
    for candidate_status in ["待生成", "生成中", "未生成"]:
        try:
            records = await dingtalk.list_records(
                base_id=base_id,
                sheet_id=sheet_id,
                field=table_config.result_status_field,
                value=candidate_status,
            )
        except Exception as e:
            print(f"  · 状态='{candidate_status}' 失败: {type(e).__name__}: {e}")
            continue

        # 本地二次过滤：素材图必须非空
        good = [r for r in records if _get_field(r, ref_field)]
        if good:
            print(f"  · 按 状态='{candidate_status}' 筛到 {len(good)} 条（已过滤素材图）")
            return _get_id(good[0])

    # 策略 3：拉一批 records，本地过滤（用任意常用字段，SDK 必须传 field/value）
    fallback_field = table_config.style_code_field or table_config.goods_id_field or "款号"
    try:
        records = await dingtalk.list_records(
            base_id=base_id,
            sheet_id=sheet_id,
            field=fallback_field,
            value="%",  # 多数 SDK % 是通配符；不支持就退到下一步
            max_results=50,
        )
    except Exception:
        # 退路：精确匹配空字符串
        records = await dingtalk.list_records(
            base_id=base_id,
            sheet_id=sheet_id,
            field=fallback_field,
            value="",
        )

    good = [r for r in records if _get_field(r, ref_field)]
    if good:
        print(f"  · 兜底扫描拉到 {len(records)} 条，本地过滤出 {len(good)} 条有素材图")
        return _get_id(good[0])
    return None


async def main() -> int:
    _check_credentials()

    # 延后导入（_check_credentials 失败时不要触发依赖）
    from config import Settings
    from dingtalk.client import DingTalkClient
    from generator import AIGenerator
    from services.generation import GenerationService

    settings = Settings()
    table_config = settings.get_table("zhuozhi-sousuo")

    print("=" * 70)
    print("搜推素材端到端测试")
    print("=" * 70)
    print(f"base_id       = {table_config.base_id}")
    print(f"sheet_id      = {table_config.sheet_id}")
    print(f"task_name     = {table_config.task_name}")
    print(f"prompt_table  = {table_config.prompt_table_sheet_id}")
    print(f"output_order  = {table_config.output_order}")
    print(f"count/section = {table_config.count_per_section}")
    print()

    # 构造客户端 + 服务
    dingtalk = DingTalkClient(settings)
    generator = AIGenerator(settings)
    service = GenerationService(dingtalk=dingtalk, generator=generator, settings=settings)

    # 1. 自动挑记录
    print("[1/4] 从 zhuozhi-sousuo 表自动找一条素材图非空的记录...")
    record_id = await _pick_record_id(dingtalk, table_config)
    if not record_id:
        print("[FAIL] 没找到素材图已填的记录。请先在钉钉表里手动填一条测试数据。")
        return 1
    print(f"  -> 选中 record_id = {record_id}")
    print()

    # 2. 启动流程
    print("[2/4] 启动生图流程（异步后台执行，不阻塞）...")
    task = asyncio.create_task(service.process(record_id, "zhuozhi-sousuo"))
    print(f"  -> 已调度 task，等待完成...")
    print()

    # 3. 等任务跑完
    try:
        await task
    except Exception as e:
        print(f"[FAIL] 流程异常: {type(e).__name__}: {e}")
        return 1

    # 4. 回查表，验证结果
    print("[3/4] 回查表格记录，验证附件是否已写入...")
    record = await dingtalk.get_record(table_config, record_id)
    fields = record.get("fields", {})
    attachments = fields.get(table_config.result_image_field) or []
    status = fields.get(table_config.result_status_field) or ""
    gen_time = fields.get(table_config.result_time_field) or ""

    print(f"  result_image_field ({table_config.result_image_field}): {len(attachments)} 个附件")
    for att in attachments[:9]:
        print(f"    - {att.get('filename')}")
    print(f"  result_status_field ({table_config.result_status_field}): {status}")
    print(f"  result_time_field  ({table_config.result_time_field}): {gen_time}")
    print()

    if len(attachments) == 3 and "成功" in str(status):
        print("[OK] 3 张图全部上传成功，去钉钉表查看吧：")
        print(f"     https://alidocs.dingtalk.com/i/notable/?baseId={table_config.base_id}&sheetId={table_config.sheet_id}&rowId={record_id}")
        return 0

    if len(attachments) == 3:
        print("[OK] 3 张图全部上传成功（状态字段是 singleSelect 枚举，未写入 '成功3/3'）。")
        print("     去钉钉表看附件即可：")
        print(f"     https://alidocs.dingtalk.com/i/notable/?baseId={table_config.base_id}&sheetId={table_config.sheet_id}&rowId={record_id}")
        return 0

    print("[PARTIAL] 流程跑完但未全部成功，去钉钉表看状态字段详情。")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))