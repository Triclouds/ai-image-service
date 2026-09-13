#!/usr/bin/env python3
"""卓芝搭配生图（zhuozhi-genMatch）配置自检 + 真实端到端测试。

搭配生图模块特点：多参考图输入（1 张【模特图】=图1，N 张【素材图】=图2/图3/...），
单图输出；提示词内文通过"图1""图2"引用对应图片；【比例】必填、【分辨率】可选。

两种运行模式
------------
1) 默认（不带 --run）：**只读自检**，不花钱、不写钉钉表。
     python tests/run_gen_match_real.py
   依次校验：
     [1] config.toml 里 zhuozhi-genMatch 表配置是否完整、开关是否正确
     [2] .env 凭证（钉钉三件套 + image_api_key_env 指定的 Key）是否齐全
     [3] 生图模型配置是否可解析
     [4] 连真实钉钉表，扫描记录，核对配置的列名在表里是否真实存在
     [5] 挑一条可用记录，预演校验（比例白名单 / 模特图 / 素材图 / 提示词）

2) 带 --run：在自检全部通过后，跑**真实**生图流程（调 AI + 回写钉钉表）。
     python tests/run_gen_match_real.py --run
     python tests/run_gen_match_real.py --run --record-id <记录ID>

凭证加载顺序（与主应用一致）：
  1. .env
  2. configs/.env
不了解/未硬编码的配置（AppKey、AppSecret、OperatorId、各家 image API Key）
全部从 .env 读取，脚本本身不含任何密钥。

退出码：0=通过  1=配置/凭证/连通性失败  2=流程跑完但结果不符合预期
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# 让脚本能 import src 包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402  (必须在 sys.path 引导之后导入)

# 把 CWD 固定到项目根（Settings 按相对路径找 configs/config.toml），
# 这样从任意目录调用本脚本都能读到同一份配置。
os.chdir(PROJECT_ROOT)

# 先加载 .env，再加载 configs/.env（已有 env 不覆盖）
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

TABLE_KEY = "zhuozhi-genMatch"  # 默认值；可用 --table-key 切换到 ahmi-genMatch / huapu-genMatch

# 钉钉基础凭证（与表无关，全局共用）
_BASE_CREDENTIALS = [
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_OPERATOR_ID",
]


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _info(msg: str) -> None:
    print(f"  ·      {msg}")


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * 70)


def _mask(value: str) -> str:
    """密钥脱敏，只留头尾。"""
    if not value:
        return "<空>"
    if len(value) <= 10:
        return value[:2] + "***"
    return f"{value[:6]}***{value[-4:]}（长度 {len(value)}）"


def _get_field(rec, key):
    """兼容 SDK 对象和 dict 两种 records 形态。"""
    fields = getattr(rec, "fields", None) or (
        rec.get("fields", {}) if isinstance(rec, dict) else {}
    )
    if not isinstance(fields, dict):
        return None
    return fields.get(key)


def _all_fields(rec) -> dict:
    fields = getattr(rec, "fields", None) or (
        rec.get("fields", {}) if isinstance(rec, dict) else {}
    )
    return fields if isinstance(fields, dict) else {}


def _get_id(rec):
    if isinstance(rec, dict):
        return rec.get("id")
    return getattr(rec, "record_id", None) or getattr(rec, "id", None)


def _attachment_count(value) -> int:
    return len(value) if isinstance(value, list) else 0


# 提示词里指代参考图的写法：阿拉伯数字与中文数字混用（"将图一复刻图2的场景"）
_CN_NUMERALS = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _refers_to(text: str, index: int) -> bool:
    """判断提示词是否引用了第 index 张参考图（图1 / 图一 两种写法都算）。"""
    if f"图{index}" in text:
        return True
    return 1 <= index < len(_CN_NUMERALS) and f"图{_CN_NUMERALS[index]}" in text


# --------------------------------------------------------------------------
# [1] config.toml 表配置自检
# --------------------------------------------------------------------------

def check_table_config(settings) -> tuple[object | None, bool]:
    """校验 zhuozhi-genMatch 的 [[dingtalk.tables]] 配置。"""
    _section(f"[1/5] 校验 config.toml 表配置：{TABLE_KEY}")

    from utils.exceptions import ConfigError

    try:
        table = settings.get_table(TABLE_KEY)
    except ConfigError as e:
        _fail(f"未找到表配置: {e}")
        _info("请确认 configs/config.toml 中存在 [[dingtalk.tables]] key = \"zhuozhi-genMatch\"")
        return None, False

    _ok(f"找到表配置 key = {table.key}")
    _info(f"base_id            = {table.base_id}")
    _info(f"sheet_id           = {table.sheet_id}")
    _info(f"image_api_key_env  = {table.image_api_key_env}")

    passed = True

    # 模式开关：搭配生图必须 gen_match_mode=true 且 batch_mode=false（单图输出）
    if table.gen_match_mode:
        _ok("gen_match_mode = true（搭配生图模块已启用）")
    else:
        _fail("gen_match_mode = false —— 搭配生图不会生效，会走普通单图流程")
        passed = False

    if table.batch_mode:
        _fail("batch_mode = true —— 搭配生图是单图输出，必须为 false，否则会被批量流程截胡")
        passed = False
    else:
        _ok("batch_mode = false（单图输出，正确）")

    # 与其他模块互斥，避免分流被抢
    for flag in ("prompt_ad_mode", "prompt_section_mode", "base_material_mode"):
        if getattr(table, flag, False):
            _fail(f"{flag} = true —— 与 gen_match_mode 冲突，请关闭")
            passed = False

    # 必填字段（对应 config.py::_validate_batch_mode_config 的启动校验）
    required = {
        "model_image_field": table.model_image_field,      # 图1
        "aspect_ratio_field": table.aspect_ratio_field,    # 必填，白名单校验
    }
    for name, value in required.items():
        if value:
            _ok(f"{name:<20}= {value}")
        else:
            _fail(f"{name:<20}未配置（gen_match_mode=true 时必填，服务启动会直接报错）")
            passed = False

    # 其余字段映射
    _ok(f"{'prompt_field':<20}= {table.prompt_field}")
    _ok(f"{'reference_image_field':<20}= {table.reference_image_field}")
    _ok(f"{'model_field':<20}= {table.model_field}")
    _ok(f"{'result_image_field':<20}= {table.result_image_field}")
    _ok(f"{'result_status_field':<20}= {table.result_status_field}")
    _ok(f"{'result_time_field':<20}= {table.result_time_field}")

    if table.resolution_field:
        _ok(f"{'resolution_field':<20}= {table.resolution_field}（可选，空值 → None）")
    else:
        _warn("resolution_field 未配置 —— 分辨率将始终按 None 处理")

    # 模特图 / 素材图不能是同一列，否则图1 与 图2 会取到同一批附件
    if table.model_image_field and table.model_image_field == table.reference_image_field:
        _fail("model_image_field 与 reference_image_field 是同一列，图1/图2 会重复")
        passed = False

    return table, passed


# --------------------------------------------------------------------------
# [2] .env 凭证自检
# --------------------------------------------------------------------------

def check_credentials(settings, table) -> bool:
    """校验 .env 中的钉钉凭证与该表指定的生图 API Key。"""
    _section("[2/5] 校验 .env 凭证")

    passed = True

    for key in _BASE_CREDENTIALS:
        value = os.environ.get(key, "")
        if value:
            _ok(f"{key:<24}= {_mask(value)}")
        else:
            _fail(f"{key:<24}缺失")
            passed = False

    # 该表用哪个 Key 由 config.toml 的 image_api_key_env 指定，不写死
    api_key_env = table.image_api_key_env
    from utils.exceptions import ConfigError

    try:
        api_key = settings.get_api_key(api_key_env)
        _ok(f"{api_key_env:<24}= {_mask(api_key)}")
    except ConfigError:
        _fail(f"{api_key_env:<24}缺失（config.toml 指定本表用这个 Key）")
        _info(f"请在 .env 或 configs/.env 中补充：{api_key_env}=<你的值>")
        passed = False

    if not passed:
        _info("提示：脚本不含任何密钥，全部从 .env / configs/.env 读取。")

    return passed


# --------------------------------------------------------------------------
# [3] 生图模型配置自检
# --------------------------------------------------------------------------

def check_model_config(settings) -> bool:
    """校验默认模型可解析，并列出全部可选模型。"""
    _section("[3/5] 校验生图模型配置")

    from utils.exceptions import ConfigError

    models = settings.ai.models
    if not models:
        _fail("config.toml 未配置任何 [ai.model.\"xxx\"]")
        return False

    for name, cfg in models.items():
        _info(f"{name:<18} provider={cfg.provider:<8} model_name={cfg.model_name}")

    default_model = settings.ai.default_model
    try:
        settings.get_model(default_model)
        _ok(f"默认模型可解析：{default_model}")
    except ConfigError as e:
        _fail(f"默认模型不可解析：{e}")
        return False

    _info("钉钉表【生图模型】列留空时，回落到上面这个默认模型")
    return True


# --------------------------------------------------------------------------
# [4] 连真实钉钉表，核对列名
# --------------------------------------------------------------------------

async def scan_records(dingtalk, table, limit: int = 50) -> list:
    """拉一批记录用于核对列名 / 挑测试记录。

    注意：DingTalkClient.list_records 强制带 field/value 精确过滤，
    而本表的「生成结果」是自由文本（如 "失败: ..."）、「比例」等是单选字典，
    equal 过滤基本匹配不上。所以这里直接调 SDK 做**不带过滤**的全量列取。
    """
    from alibabacloud_dingtalk.notable_1_0 import models as notable_models
    from alibabacloud_tea_util import models as util_models

    token = await dingtalk._get_access_token()
    headers = notable_models.ListRecordsHeaders()
    headers.x_acs_dingtalk_access_token = token

    collected: list = []
    next_token = None

    while len(collected) < limit:
        request = notable_models.ListRecordsRequest(
            operator_id=dingtalk.operator_id,
            max_results=min(limit - len(collected), 100),
            next_token=next_token,
        )
        response = await dingtalk._client.list_records_with_options_async(
            base_id=table.base_id,
            sheet_id_or_name=table.sheet_id,
            request=request,
            headers=headers,
            runtime=util_models.RuntimeOptions(),
        )
        body = getattr(response, "body", None)
        if body is None:
            break
        collected.extend(body.records or [])
        next_token = getattr(body, "next_token", None)
        if not next_token or not body.records:
            break

    return collected[:limit]


async def check_dingtalk_table(dingtalk, table) -> tuple[list, bool]:
    """连真实表，核对 config.toml 里配置的列名是否真的存在。"""
    _section("[4/5] 连接真实钉钉表，核对列名")

    try:
        records = await scan_records(dingtalk, table)
    except Exception as e:
        _fail(f"连接钉钉失败: {type(e).__name__}: {e}")
        _info("请检查 DINGTALK_APP_KEY / APP_SECRET / OPERATOR_ID 以及应用对该表的授权")
        return [], False

    if not records:
        _warn("连上了钉钉，但没扫到任何记录 —— 无法核对列名")
        _info("请先在钉钉表里手动填一条测试数据（模特图 + 素材图 + 提示词 + 比例）")
        return [], False

    _ok(f"连接成功，扫到 {len(records)} 条记录")

    # 多条记录取并集：钉钉只返回非空单元格，单条记录判断列是否存在不可靠
    seen_columns: set[str] = set()
    for rec in records:
        seen_columns.update(_all_fields(rec).keys())

    _info(f"表中实际出现过的列（{len(seen_columns)} 个）：{', '.join(sorted(seen_columns))}")
    print()

    configured = {
        "model_field": table.model_field,
        "prompt_field": table.prompt_field,
        "model_image_field": table.model_image_field,
        "reference_image_field": table.reference_image_field,
        "aspect_ratio_field": table.aspect_ratio_field,
        "resolution_field": table.resolution_field,
        "result_image_field": table.result_image_field,
        "result_status_field": table.result_status_field,
        "result_time_field": table.result_time_field,
    }

    passed = True
    # 结果回写列（生成图片/生成结果/生成时间）新行必为空，钉钉 list_records
    # 不返回空字段，self-check 看不到不代表列不存在，列为写回时不强制校验。
    optional = {
        "resolution_field",
        "result_image_field",  # attachment 列，新行附件为空
        "result_status_field",  # 状态列，新行还没跑过必为空
        "result_time_field",
    }
    for name, column in configured.items():
        if not column:
            continue
        if column in seen_columns:
            _ok(f"{name:<22}-> 列「{column}」存在")
        elif name in optional:
            _warn(
                f"{name:<22}-> 列「{column}」在样本里没出现"
                f"（结果是写回列，新行正常为空；不阻断）"
            )
        else:
            _fail(f"{name:<22}-> 列「{column}」在 {len(records)} 条样本里从未出现，疑似列名不匹配")
            passed = False

    return records, passed


# --------------------------------------------------------------------------
# [5] 挑记录 + 预演校验
# --------------------------------------------------------------------------

def pick_candidate(records, table):
    """挑一条【模特图】和【素材图】都非空的记录。"""
    for rec in records:
        model_imgs = _get_field(rec, table.model_image_field)
        ref_imgs = _get_field(rec, table.reference_image_field)
        prompt = _get_field(rec, table.prompt_field)
        if _attachment_count(model_imgs) >= 1 and _attachment_count(ref_imgs) >= 1 and prompt:
            return rec
    return None


def dry_run_validate(rec, table, settings) -> bool:
    """按 _process_gen_match 的真实校验顺序预演一遍，不调 AI、不写表。"""
    _section("[5/5] 预演校验（复刻 _process_gen_match 的校验逻辑，不调 AI）")

    from models.prompt_config import (
        _VALID_ASPECT_RATIOS,
        AspectRatioError,
        _to_text,
        _validate_aspect_ratio,
    )

    record_id = _get_id(rec)
    fields = _all_fields(rec)
    _ok(f"选中 record_id = {record_id}")

    passed = True

    # 1. 比例（必填 + 白名单）
    raw_ratio = fields.get(table.aspect_ratio_field)
    try:
        ratio = _validate_aspect_ratio(raw_ratio)
        _ok(f"比例校验通过：「{table.aspect_ratio_field}」= {ratio}")
    except AspectRatioError as e:
        _fail(f"比例校验失败：{e}")
        _info(f"合法值：{', '.join(sorted(_VALID_ASPECT_RATIOS))}")
        passed = False

    # 2. 分辨率（可选）
    if table.resolution_field:
        resolution = _to_text(fields.get(table.resolution_field)) or None
        if resolution:
            _ok(f"分辨率：「{table.resolution_field}」= {resolution}")
            if resolution not in {"1K", "2K", "4K"}:
                _warn(f"分辨率 {resolution} 不在常见档位 1K/2K/4K 中，请确认下游支持")
        else:
            _ok(f"分辨率：「{table.resolution_field}」为空 → None（可选，允许）")

    # 3. 模特图 = 图1
    model_imgs = fields.get(table.model_image_field)
    model_count = _attachment_count(model_imgs)
    if model_count >= 1:
        _ok(f"模特图：{model_count} 张 → 取第 1 张作为【图1】")
        if model_count > 1:
            _warn(f"模特图有 {model_count} 张，只有第 1 张会用作图1，其余被忽略")
        for att in (model_imgs or [])[:1]:
            _info(f"图1 = {att.get('filename', '未知')}")
    else:
        _fail(f"模特图为空（列「{table.model_image_field}」）—— 流程会直接写失败退出")
        passed = False

    # 4. 素材图 = 图2/图3/...
    ref_imgs = fields.get(table.reference_image_field)
    ref_count = _attachment_count(ref_imgs)
    if ref_count >= 1:
        _ok(f"素材图：{ref_count} 张 → 依次作为【图2】…【图{ref_count + 1}】")
        for idx, att in enumerate(ref_imgs or [], start=2):
            _info(f"图{idx} = {att.get('filename', '未知')}")
    else:
        _fail(f"素材图为空（列「{table.reference_image_field}」）—— 流程会直接写失败退出")
        passed = False

    # 5. 提示词
    prompt = fields.get(table.prompt_field)
    if prompt:
        text = _to_text(prompt)
        _ok(f"提示词非空，长度 {len(text)}")
        _info(f"预览：{text[:100]}{'...' if len(text) > 100 else ''}")
        # 搭配生图靠"图N"指代参考图。实际表里阿拉伯数字和中文数字混用
        # （例："将图一复刻图2的场景"），两种写法都要认。
        total = 1 + ref_count
        referenced = [f"图{i}" for i in range(1, total + 1) if _refers_to(text, i)]
        if referenced:
            _ok(f"提示词中引用了：{', '.join(referenced)}（本次共 {total} 张参考图）")
            missing = [f"图{i}" for i in range(1, total + 1) if not _refers_to(text, i)]
            if missing:
                _warn(f"未被引用的参考图：{', '.join(missing)} —— 模型可能用不上这几张")
        else:
            _warn(
                f"提示词里没出现「图1」「图一」等指代（本次共 {total} 张参考图），"
                "模型可能分不清哪张是哪张"
            )
        over = [f"图{i}" for i in range(total + 1, total + 5) if _refers_to(text, i)]
        if over:
            _warn(f"提示词引用了不存在的 {', '.join(over)} —— 实际只有 {total} 张参考图")
    else:
        _fail(f"提示词为空（列「{table.prompt_field}」）")
        passed = False

    # 6. 模型解析（钉钉单选列返回 {"name": ...}，需归一化后再查配置）
    model_raw = fields.get(table.model_field)
    model_name = _to_text(model_raw).strip()
    if not model_name:
        model_name = settings.ai.default_model
        _ok(f"生图模型：「{table.model_field}」为空 → 回落默认模型 {model_name}")
    else:
        _ok(f"生图模型：「{table.model_field}」= {model_name}")

    from utils.exceptions import ConfigError

    try:
        model_cfg = settings.get_model(model_name)
        _ok(f"模型配置可解析：provider={model_cfg.provider}, model_name={model_cfg.model_name}")
    except ConfigError:
        _fail(f"模型「{model_name}」在 config.toml 的 [ai.model.*] 中不存在，生图会直接失败")
        _info(f"已配置的模型：{', '.join(settings.ai.models.keys())}")
        passed = False

    return passed


# --------------------------------------------------------------------------
# 真实生图（--run）
# --------------------------------------------------------------------------

async def run_real_generation(service, dingtalk, table, record_id: str) -> int:
    """真实跑一遍搭配生图流程，然后回查表格验证结果。"""
    _section("[E2E] 真实生图流程（调 AI + 回写钉钉表）")

    print(f"  record_id = {record_id}")
    print("  执行中，视模型耗时可能需要 30s ~ 数分钟...")
    print()

    try:
        await service.process(record_id, TABLE_KEY)
    except Exception as e:
        _fail(f"流程异常: {type(e).__name__}: {e}")
        return 1

    # 回查表格验证
    _section("[E2E] 回查记录，验证回写结果")
    record = await dingtalk.get_record(table, record_id)
    fields = record.get("fields", {})

    attachments = fields.get(table.result_image_field) or []
    status = fields.get(table.result_status_field) or ""
    gen_time = fields.get(table.result_time_field) or ""

    _info(f"{table.result_image_field}: {len(attachments)} 个附件")
    for att in attachments:
        _info(f"  - {att.get('filename')}")
    _info(f"{table.result_status_field}: {status}")
    _info(f"{table.result_time_field}: {gen_time}")

    url = (
        f"https://alidocs.dingtalk.com/i/notable/?baseId={table.base_id}"
        f"&sheetId={table.sheet_id}&rowId={record_id}"
    )

    # 关键：判断表里数据是不是这次跑出来的。
    # 钉钉返回的生成时间是字符串化的毫秒戳（少数情况下是 datetime），
    # 距 process() 开始 > 5 分钟就视作旧残留（之前 E2E 跑出来的图 + 当前这次失败状态）。
    gen_time_ms: int | None = None
    if isinstance(gen_time, (int, float)):
        gen_time_ms = int(gen_time)
    elif isinstance(gen_time, str) and gen_time.strip().isdigit():
        gen_time_ms = int(gen_time.strip())
    now_ms = int(time.time() * 1000)
    age_min = None
    if gen_time_ms is not None:
        age_min = (now_ms - gen_time_ms) / 60_000
    is_fresh = gen_time_ms is not None and age_min is not None and age_min < 5
    if gen_time_ms is not None:
        _info(f"  生成时间距今 {age_min:.1f} 分钟（{ '新鲜 = 本次 E2E 产物' if is_fresh else '陈旧 = 之前 E2E 残留' }）")

    print()
    # 1. 期望恰好 1 张附件（搭配生图单图输出）
    if len(attachments) > 1:
        _fail(f"期望单图输出，实际写入 {len(attachments)} 个附件 —— 检查 batch_mode 是否误开")
        print(f"\n  去钉钉表查看：{url}")
        return 2

    # 2. 如果状态明确说失败 / 不在新鲜窗口 → 失败
    status_str = str(status)
    is_success = status_str == "成功"
    is_failure = status_str.startswith("失败") or status_str.startswith("AI 生图成功")

    if is_failure or not is_fresh:
        # 区分：新鲜失败 vs 陈旧残留
        if not is_fresh and len(attachments) == 1:
            _fail(
                f"表里残留上一次 E2E 的图（{age_min:.0f} 分钟前），"
                f"本次实际状态=「{status_str}」—— 不要被「1 个附件」误导"
            )
        else:
            _fail(f"未成功：状态=「{status_str}」")
        print(f"\n  去钉钉表查看：{url}")
        return 2

    # 3. 1 张附件 + 状态=成功 + 时间新鲜 = 真成功
    if len(attachments) == 1 and is_success:
        _ok(f"搭配生图成功：单图输出 1 张，状态=成功（生成于 {age_min:.1f} 分钟前）")
        print(f"\n  去钉钉表查看：{url}")
        return 0

    # 兜底
    _fail(f"未识别状态：附件={len(attachments)} 状态=「{status_str}」 时间新鲜度={is_fresh}")
    print(f"\n  去钉钉表查看：{url}")
    return 2


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

async def main() -> int:
    global TABLE_KEY
    parser = argparse.ArgumentParser(
        description="搭配生图（*-genMatch）配置自检 + 真实端到端测试，"
                    "默认 zhuozhi-genMatch，可用 --table-key 切换到 ahmi/huapu",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="自检通过后，真实调用 AI 生图并回写钉钉表（会产生费用）",
    )
    parser.add_argument(
        "--record-id",
        default=None,
        help="指定测试用的记录 ID；不填则自动挑一条模特图/素材图都非空的记录",
    )
    parser.add_argument(
        "--table-key",
        default=TABLE_KEY,
        help=f"目标 table key（默认 {TABLE_KEY}；常用 ahmi-genMatch / huapu-genMatch）",
    )
    args = parser.parse_args()
    TABLE_KEY = args.table_key

    print("=" * 70)
    print(f"搭配生图（{TABLE_KEY}）配置测试")
    print("=" * 70)
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"配置文件  : {os.environ.get('CONFIG_PATH', 'configs/config.toml')}")
    mode_desc = "真实生图 E2E（--run）" if args.run else "只读自检（加 --run 跑真实生图）"
    print(f"运行模式  : {mode_desc}")

    # 加载配置（Settings.__init__ 会跑一遍启动期校验，配置有硬伤这里就会抛）
    try:
        from config import Settings
    except Exception as e:
        print(f"\n[FAIL] 导入 config 失败: {type(e).__name__}: {e}")
        return 1

    try:
        settings = Settings()
    except Exception as e:
        print(f"\n[FAIL] 加载配置失败（启动期校验未通过）: {type(e).__name__}: {e}")
        return 1

    # [1] 表配置
    table, ok_table = check_table_config(settings)
    if table is None:
        print("\n[FAIL] 表配置缺失，后续检查无法进行。")
        return 1

    # [2] 凭证
    ok_env = check_credentials(settings, table)

    # [3] 模型
    ok_model = check_model_config(settings)

    if not (ok_table and ok_env and ok_model):
        print()
        print("=" * 70)
        print("[FAIL] 本地配置自检未通过，已跳过钉钉连通性测试。请先修复上面的 [FAIL] 项。")
        print("=" * 70)
        return 1

    # [4] 连钉钉核对列名
    from dingtalk.client import DingTalkClient

    dingtalk = DingTalkClient(settings)
    records, ok_columns = await check_dingtalk_table(dingtalk, table)

    if not records:
        print()
        print("=" * 70)
        print("[FAIL] 未能从钉钉表读到记录，无法完成列名核对与预演校验。")
        print("=" * 70)
        return 1

    # [5] 挑记录 + 预演
    if args.record_id:
        try:
            rec = await dingtalk.get_record(table, args.record_id)
            rec = {"id": args.record_id, "fields": rec.get("fields", {})}
        except Exception as e:
            print(f"\n[FAIL] 指定的 record_id 读取失败: {type(e).__name__}: {e}")
            return 1
    else:
        rec = pick_candidate(records, table)

    if rec is None:
        _section("[5/5] 预演校验")
        _fail("样本里没有一条记录同时满足：模特图非空 + 素材图非空 + 提示词非空")
        _info("请在钉钉表里手动填一条完整的测试数据后重跑")
        print()
        print("=" * 70)
        print("[FAIL] 缺少可用测试数据。")
        print("=" * 70)
        return 1

    ok_dry = dry_run_validate(rec, table, settings)
    record_id = _get_id(rec)

    # 汇总
    print()
    print("=" * 70)
    all_ok = ok_table and ok_env and ok_model and ok_columns and ok_dry
    if all_ok:
        print("[OK] 配置自检全部通过。")
    else:
        print("[FAIL] 自检存在未通过项（见上方 [FAIL]）。")
    print("=" * 70)

    if not args.run:
        print()
        if all_ok:
            print("如需真实跑一遍生图（会调用 AI 并回写钉钉表，产生费用）：")
            print(f"  python tests/run_gen_match_real.py --run --record-id {record_id}")
        return 0 if all_ok else 1

    if not all_ok:
        print("\n[ABORT] 自检未通过，已拒绝执行真实生图。请先修复配置。")
        return 1

    # --run：真实跑流程
    from generator import AIGenerator
    from services.generation import GenerationService

    generator = AIGenerator(settings)
    service = GenerationService(dingtalk=dingtalk, generator=generator, settings=settings)

    return await run_real_generation(service, dingtalk, table, record_id)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
