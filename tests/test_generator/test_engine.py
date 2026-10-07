"""AIGenerator.generate_batch 单元测试。"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from loguru import logger

from config import TableConfig
from generator.engine import AIGenerator


def _table_config() -> TableConfig:
    return TableConfig(
        key="batch-test",
        base_id="tbl_test",
        sheet_id="sheet_test",
        image_api_key_env="TEST_IMAGE_API_KEY",
    )


def test_batch_concurrency_constant():
    """_BATCH_CONCURRENCY 固定为 3（计划文档规定）。"""
    assert AIGenerator._BATCH_CONCURRENCY == 3


@pytest.mark.asyncio
async def test_generate_batch_empty_prompts_returns_empty_list(mock_settings):
    """prompts 为空 → 返回 []，不调 generate。"""
    gen = AIGenerator(mock_settings)
    gen.generate = AsyncMock()

    result = await gen.generate_batch(
        model="Nano Banana 2", prompts=[], table_config=_table_config()
    )

    assert result == []
    gen.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_batch_all_success_preserves_order(mock_settings):
    """全成功：结果顺序与 prompts 一致。"""
    gen = AIGenerator(mock_settings)
    bytes_seq = [b"img_1", b"img_2", b"img_3"]

    async def fake_generate(model, prompt, reference_image=None, table_config=None, **kwargs):
        # 按 prompt 后缀取对应 bytes
        idx = int(prompt.split("_")[1]) - 1
        return bytes_seq[idx]

    gen.generate = fake_generate

    result = await gen.generate_batch(
        model="Nano Banana 2",
        prompts=["p_1", "p_2", "p_3"],
        table_config=_table_config(),
    )

    assert result == [b"img_1", b"img_2", b"img_3"]


@pytest.mark.asyncio
async def test_generate_batch_partial_failure_returns_none(mock_settings):
    """单张失败 → 返回 [None, ...]。"""
    gen = AIGenerator(mock_settings)

    async def fake_generate(model, prompt, reference_image=None, table_config=None, **kwargs):
        if prompt == "bad":
            raise ValueError("boom")
        return prompt.encode()

    gen.generate = fake_generate

    result = await gen.generate_batch(
        model="Nano Banana 2",
        prompts=["ok", "bad", "ok2"],
        table_config=_table_config(),
    )

    assert result[0] == b"ok"
    assert result[1] is None
    assert result[2] == b"ok2"


@pytest.mark.asyncio
async def test_generate_batch_concurrency_limit_enforced(mock_settings):
    """N 张 prompt 实际并发 ≤ _BATCH_CONCURRENCY（=3）。"""
    gen = AIGenerator(mock_settings)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def fake_generate(model, prompt, reference_image=None, table_config=None, **kwargs):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.02)
            return prompt.encode()
        finally:
            async with lock:
                in_flight -= 1

    gen.generate = fake_generate

    prompts = [f"p_{i}" for i in range(9)]
    result = await gen.generate_batch(
        model="Nano Banana 2", prompts=prompts, table_config=_table_config()
    )

    assert len(result) == 9
    assert max_in_flight <= AIGenerator._BATCH_CONCURRENCY


# ─────────── _retry_on_network_error（网络异常重试日志）───────────


@pytest.mark.asyncio
async def test_retry_on_network_error_logs_warning(mock_settings, log_records):
    """首次网络异常 → 打 WARNING 重试日志（含 func / attempt / error）→ 第二次成功。

    重试日志必须延续请求链路的 request_id（重试属于同一请求的后续链路）。
    """
    gen = AIGenerator(mock_settings)
    calls = {"n": 0}

    async def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("连接失败")
        return b"ok"

    with logger.contextualize(request_id="REQ_TEST"):
        result = await gen._retry_on_network_error(_flaky)

    assert result == b"ok"
    assert calls["n"] == 2

    warnings = [r for r in log_records if r["level"].name == "WARNING"]
    assert len(warnings) == 1
    rec = warnings[0]
    assert "网络异常" in rec["message"]
    assert "重试" in rec["message"]
    assert rec["extra"]["func"] == "_flaky"
    assert rec["extra"]["attempt"] == 1
    assert "连接失败" in rec["extra"]["error"]
    assert rec["extra"]["request_id"] == "REQ_TEST"


@pytest.mark.asyncio
async def test_retry_on_network_error_exhausted_raises(mock_settings, log_records):
    """重试耗尽 → 抛出最后一次异常，且每次重试前各有一条 WARNING。"""
    gen = AIGenerator(mock_settings)
    calls = {"n": 0}

    async def _always_fail():
        calls["n"] += 1
        raise httpx.TimeoutException("timeout")

    with logger.contextualize(request_id="REQ_TEST"):
        with pytest.raises(httpx.TimeoutException):
            await gen._retry_on_network_error(_always_fail)

    assert calls["n"] == 2  # max_retries=1 → 首次 + 1 次重试
    warnings = [r for r in log_records if r["level"].name == "WARNING"]
    assert len(warnings) == 1  # 仅首次失败后打日志，最后一次失败直接抛出
    assert warnings[0]["extra"]["attempt"] == 1