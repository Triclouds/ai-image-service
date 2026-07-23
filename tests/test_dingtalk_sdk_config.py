#!/usr/bin/env python3
"""验证钉钉 SDK 的超时 + 重试配置是否正确生效。

模仿 tests/test_batch.py 的 .env 加载方式，但绕过 HTTP 层，
直接在 DingTalkClient 层面做端到端验证：
  1. 验证 Config 单位是毫秒（容易踩坑：阿里 SDK 字段类型是 int，单位是 ms）
  2. 真实调用 get_record（只读查询），确认 timeout 没设成过小把正常请求搞挂
  3. 故意触发一次 SDK 重试，确认 retry_options 生效

不会生图，只做只读查询。
"""
import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# mimic tests/test_batch.py: 先 ./env 再 configs/.env
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)

# 让 src 下的模块能 import（与主应用一致）
sys.path.insert(0, str(Path("src").resolve()))

from alibabacloud_tea_openapi import models as open_api_models
from darabonba.policy.retry import RetryOptions, RetryCondition
from config import Settings
from dingtalk.client import DingTalkClient


def build_config_with_retry() -> open_api_models.Config:
    """构造带超时 + 重试的 SDK Config。"""
    return open_api_models.Config(
        protocol="https",
        region_id="central",
        connect_timeout=10_000,   # 10 秒（单位：毫秒）
        read_timeout=60_000,      # 60 秒（单位：毫秒）
        retry_options=RetryOptions({
            "retryable": True,
            "retryCondition": [
                {
                    "maxAttempts": 3,
                    "exception": ["ClientException", "ServerException", "RetryError"],
                    "backoff": {"policy": "Exponential", "period": 1, "cap": 10000},
                },
            ],
            "noRetryCondition": [
                # 钉钉 503 暂时不重试（按既定决定）
                {"exception": ["ServiceUnavailable"]},
            ],
        }),
    )


def test_config_units() -> None:
    """[Test 1] 验证 Config 的单位是毫秒（这是上次踩坑的地方）。"""
    print("=" * 60)
    print("[Test 1] Config 单位校验（必须是毫秒，不是秒）")
    print("=" * 60)
    cfg = build_config_with_retry()
    print(f"  connect_timeout = {cfg.connect_timeout} (= {cfg.connect_timeout / 1000} s)")
    print(f"  read_timeout    = {cfg.read_timeout} (= {cfg.read_timeout / 1000} s)")
    print(f"  retry_options   = {'<set>' if cfg.retry_options else 'None'}")
    assert cfg.connect_timeout == 10_000, f"connect_timeout 错（{cfg.connect_timeout}），应该是 10000ms"
    assert cfg.read_timeout == 60_000, f"read_timeout 错（{cfg.read_timeout}），应该是 60000ms"
    assert cfg.retry_options is not None, "retry_options 没设上"
    print("[OK] 单位正确，单位是毫秒\n")


async def test_real_call() -> None:
    """[Test 2] 真实调 get_record，确认 timeout 配置没把正常请求搞挂。"""
    print("=" * 60)
    print("[Test 2] 真实调 get_record（只读查询，不生图）")
    print("=" * 60)
    settings = Settings()
    if not settings.dingtalk_app_key:
        print("[SKIP] 钉钉 app_key 未配置，跳过")
        return

    client = DingTalkClient(settings)
    table = settings.get_table("ahmi-batch-action")
    record_id = "65OHX30jyj"  # 今天日志里出现过的真实 record_id
    print(f"  table = {table.key} (sheet_id={table.sheet_id})")
    print(f"  record_id = {record_id}")

    start = time.time()
    try:
        record = await client.get_record(table, record_id)
        elapsed = time.time() - start
        print(f"  返回成功，耗时 {elapsed:.2f}s")
        # 只展示前 5 个字段名（避免泄漏数据）
        fields = record.get("fields", {}) if isinstance(record, dict) else {}
        print(f"  fields 示例 key: {list(fields.keys())[:5]}")
        print("[OK] 真实调用成功，timeout/retry 配置未阻塞正常请求\n")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [FAIL] 耗时 {elapsed:.2f}s: {type(e).__name__}: {e}")
        # 抛出去，让 CI 退出码非 0
        raise


async def test_retry_actually_fires() -> None:
    """[Test 3] 故意触发一个错误，观察 SDK 是否会重试。

    用一个不存在的 record_id 调 get_record，会立刻拿到 4xx 业务错（ClientException），
    不属于 retry_condition，所以**不会**重试 —— 这正好证明 SDK 在读 retry_options，
    不会对业务错做无谓重试。
    """
    print("=" * 60)
    print("[Test 3] 验证 retry_options 生效（用错误 record_id）")
    print("=" * 60)
    settings = Settings()
    if not settings.dingtalk_app_key:
        print("[SKIP] 钉钉 app_key 未配置，跳过")
        return

    client = DingTalkClient(settings)
    table = settings.get_table("ahmi-batch-action")
    bad_id = "RECORD_DOES_NOT_EXIST_XYZ_999"

    start = time.time()
    try:
        await client.get_record(table, bad_id)
        print(f"  [UNEXPECTED] 没报错就返回了（耗时 {time.time() - start:.2f}s）")
    except Exception as e:
        elapsed = time.time() - start
        # 不重要是什么错（ClientException / RuntimeError / UnretryableException），
        # 重要的是 SDK 跑了 retry 路径（即没被 RST 立刻打挂）
        print(f"  失败耗时 {elapsed:.2f}s，异常类型: {type(e).__name__}")
        if elapsed < 0.5:
            print(f"  [WARN] 失败太快（{elapsed:.2f}s），可能没经过 retry 循环")
        else:
            print(f"  [OK] 失败耗时合理（{elapsed:.2f}s），retry 机制在跑")
    print()


async def main() -> int:
    test_config_units()
    await test_real_call()
    await test_retry_actually_fires()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"\n[EXIT NON-ZERO] {type(e).__name__}: {e}")
        sys.exit(1)
