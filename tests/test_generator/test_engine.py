"""AIGenerator.generate_batch 单元测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

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