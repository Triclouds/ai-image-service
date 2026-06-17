"""VideoGenerator 单元测试。

通过 patch httpx.AsyncClient 拦截三家厂商的 submit/poll 网络调用，
断言请求 URL、payload、状态字段路径与轮询终态解析正确。
"""

import io
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from config import VideoTableConfig
from generator import _resolve_provider
from generator.video_engine import VideoGenerator

# ─────────── helpers ───────────


def _fake_png_bytes() -> bytes:
    """生成一个最小可用的 PNG 字节，供 _to_png_bytes 解析。"""
    img = Image.new("RGB", (320, 320), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_response(status_code: int, json_body: dict, content: bytes | None = None) -> AsyncMock:
    """构造 httpx Response 风格的 mock。"""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: json_body
    resp.raise_for_status = lambda: None
    resp.content = content if content is not None else b"fake-mp4-bytes"
    return resp


def _patch_httpx_client(responses: list):
    """构造可被 async with 使用的 httpx.AsyncClient mock，依次返回 responses。

    responses 是按调用顺序的 Response mock 列表；submit 通常 1 次，poll 可能多次。
    post 与 get 共享同一队列，调用顺序即 consume 顺序。
    """
    client_mock = AsyncMock()
    queue = deque(responses)
    client_mock.post.side_effect = lambda *a, **kw: _pop_or_raise(queue, "post")
    client_mock.get.side_effect = lambda *a, **kw: _pop_or_raise(queue, "get")
    return _AsyncClientCtx(client_mock)


def _pop_or_raise(queue, name):
    if not queue:
        raise AssertionError(f"httpx {name} called more times than mocked responses")
    return queue.popleft()


class _AsyncClientCtx:
    """让 httpx.AsyncClient(...) 同时支持直接调用与 async with 协议。"""

    def __init__(self, client_mock):
        self._client = client_mock

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *args):
        return None


def _video_table() -> VideoTableConfig:
    return VideoTableConfig(
        key="zhuozhi-video",
        base_id="tbl_video",
        sheet_id="sheet_video",
        video_api_key_env="ZHUOZHI_IMAGE_API_KEY",  # 复用同品牌图片 API Key
    )


# ─────────── 路由 ───────────


def test_resolve_provider_routes():
    """model_name 前缀路由到正确的 provider。"""
    assert _resolve_provider("kling-v1") == "kling"
    assert _resolve_provider("kling-v1-5") == "kling"
    assert _resolve_provider("kling-v1-6") == "kling"
    assert _resolve_provider("kling-v2-5-turbo") == "kling"
    assert _resolve_provider("MiniMax-Hailuo-2.3") == "hailuo"
    assert _resolve_provider("minimax-hailuo-2") == "hailuo"
    assert _resolve_provider("Hailuo-Pro") == "hailuo"
    assert _resolve_provider("happyhorse-1.0-i2v") == "wanxiang"
    assert _resolve_provider("wanx-1") == "wanxiang"


def test_resolve_provider_unknown_raises():
    """未知前缀应抛 ValueError。"""
    with pytest.raises(ValueError, match="Unknown video model"):
        _resolve_provider("some-future-model")


# ─────────── Kling ───────────


@pytest.mark.asyncio
async def test_generate_kling_success(mock_settings_with_video_keys):
    """Kling 端到端成功：submit → poll (submitted → succeed) → 下载 mp4。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "code": 0,
        "message": "SUCCEED",
        "data": {"task_id": "kling_task_001", "task_status": "submitted"},
    })
    poll_resp_1 = _make_response(200, {
        "code": 0,
        "data": {"task_id": "kling_task_001", "task_status": "submitted"},
    })
    poll_resp_2 = _make_response(200, {
        "code": 0,
        "data": {
            "task_id": "kling_task_001",
            "task_status": "succeed",
            "task_result": {"videos": [{"url": "https://cdn.example.com/result.mp4"}]},
        },
    })
    download_resp = _make_response(200, {}, content=b"MP4_BYTES")

    httpx_mock = _patch_httpx_client([submit_resp, poll_resp_1, poll_resp_2, download_resp])

    gen = VideoGenerator(mock_settings_with_video_keys)
    with patch("generator.video_engine.httpx.AsyncClient", httpx_mock):
        result = await gen.generate(
            model="kling-v2-5-turbo",
            prompt="宇航员站起身走了",
            reference_image=_fake_png_bytes(),
            table_config=table,
        )

    assert result == b"MP4_BYTES"


@pytest.mark.asyncio
async def test_generate_kling_failed_status(mock_settings_with_video_keys):
    """Kling 任务失败状态应抛 RuntimeError。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "code": 0,
        "data": {"task_id": "kling_task_002", "task_status": "submitted"},
    })
    poll_resp = _make_response(200, {
        "code": 0,
        "data": {"task_id": "kling_task_002", "task_status": "failed", "task_result": {}},
    })

    httpx_mock = _patch_httpx_client([submit_resp, poll_resp])
    gen = VideoGenerator(mock_settings_with_video_keys)
    with (
        patch("generator.video_engine.httpx.AsyncClient", httpx_mock),
        pytest.raises(RuntimeError, match="kling video task failed"),
    ):
        await gen.generate(
            model="kling-v1",
            prompt="a cat",
            reference_image=_fake_png_bytes(),
            table_config=table,
        )


# ─────────── Hailuo ───────────


@pytest.mark.asyncio
async def test_generate_hailuo_success(mock_settings_with_video_keys):
    """Hailuo 端到端成功。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "task_id": "hailuo_task_001",
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })
    poll_resp = _make_response(200, {
        "task_id": "hailuo_task_001",
        "data": {"status": "Success", "file": {"download_url": "https://cdn.example.com/h.mp4"}},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })
    download_resp = _make_response(200, {}, content=b"HAILUO_MP4")

    httpx_mock = _patch_httpx_client([submit_resp, poll_resp, download_resp])
    gen = VideoGenerator(mock_settings_with_video_keys)
    with patch("generator.video_engine.httpx.AsyncClient", httpx_mock):
        result = await gen.generate(
            model="MiniMax-Hailuo-2.3",
            prompt="a pig running",
            reference_image=_fake_png_bytes(),
            table_config=table,
        )
    assert result == b"HAILUO_MP4"


@pytest.mark.asyncio
async def test_generate_hailuo_submit_failed(mock_settings_with_video_keys):
    """Hailuo 提交失败（非零 status_code）应抛 RuntimeError。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "task_id": "",
        "base_resp": {"status_code": 1001, "status_msg": "invalid params"},
    })

    httpx_mock = _patch_httpx_client([submit_resp])
    gen = VideoGenerator(mock_settings_with_video_keys)
    with (
        patch("generator.video_engine.httpx.AsyncClient", httpx_mock),
        pytest.raises(RuntimeError, match="Hailuo submit failed"),
    ):
        await gen.generate(
            model="MiniMax-Hailuo-2.3",
            prompt="a pig",
            reference_image=_fake_png_bytes(),
            table_config=table,
            )


# ─────────── Wanxiang ───────────


@pytest.mark.asyncio
async def test_generate_wanxiang_success(mock_settings_with_video_keys):
    """Wanxiang 端到端成功。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "request_id": "req-001",
        "output": {"task_id": "wanxiang_task_001", "task_status": "PENDING"},
    })
    poll_resp = _make_response(200, {
        "request_id": "req-001",
        "output": {
            "task_id": "wanxiang_task_001",
            "task_status": "SUCCEEDED",
            "video_url": "https://cdn.example.com/w.mp4",
        },
    })
    download_resp = _make_response(200, {}, content=b"WANXIANG_MP4")

    httpx_mock = _patch_httpx_client([submit_resp, poll_resp, download_resp])
    gen = VideoGenerator(mock_settings_with_video_keys)
    with patch("generator.video_engine.httpx.AsyncClient", httpx_mock):
        result = await gen.generate(
            model="happyhorse-1.0-i2v",
            prompt="a cat on grass",
            reference_image=_fake_png_bytes(),
            table_config=table,
        )
    assert result == b"WANXIANG_MP4"


# ─────────── 超时 ───────────


@pytest.mark.asyncio
async def test_generate_timeout_raises(mock_settings_tight_poll):
    """轮询一直返回 processing，超过 max_total 后应抛 TimeoutError。"""
    table = _video_table()
    submit_resp = _make_response(200, {
        "code": 0,
        "data": {"task_id": "kling_task_003", "task_status": "submitted"},
    })
    poll_resp_processing = _make_response(200, {
        "code": 0,
        "data": {"task_id": "kling_task_003", "task_status": "processing"},
    })
    poll_responses = [poll_resp_processing] * 5
    httpx_mock = _patch_httpx_client([submit_resp, *poll_responses])

    gen = VideoGenerator(mock_settings_tight_poll)
    # tight_poll: max_total=0 → 首次轮询即超时
    with (
        patch("generator.video_engine.httpx.AsyncClient", httpx_mock),
        pytest.raises(TimeoutError, match="kling video task timeout"),
    ):
        await gen.generate(
            model="kling-v1",
            prompt="a cat",
            reference_image=_fake_png_bytes(),
            table_config=table,
            )
