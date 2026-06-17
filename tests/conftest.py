"""pytest 配置。"""

from unittest.mock import patch

import pytest

from config import (
    AiConfig,
    DingtalkConfig,
    ModelConfig,
    RetryConfig,
    ServerConfig,
    Settings,
    TableConfig,
    VideoPollConfig,
    VideoProviderConfig,
    VideoTableConfig,
)
from main import create_app


@pytest.fixture
def mock_settings():
    """测试用配置。"""
    settings = Settings(
        dingtalk_app_key="test_app_key",
        dingtalk_app_secret="test_app_secret",
        dingtalk_operator_id="test_operator_id",
        api_key="test_api_key",
        server=ServerConfig(
            host="0.0.0.0", port=8030, log_level="INFO",
            max_concurrency=5, video_max_concurrency=3,
        ),
        ai=AiConfig(
            default_model="Nano Banana 2",
            retry=RetryConfig(initial_delay=1, max_retries=1),
            models={
                "Nano Banana Pro": ModelConfig(
                    base_url="https://api.vectorengine.ai",
                    model_name="gemini-3-pro-image-preview",
                    provider="google",
                ),
                "Nano Banana 2": ModelConfig(
                    base_url="https://api.vectorengine.ai",
                    model_name="gemini-3.1-flash-image-preview",
                    provider="google",
                ),
                "GPT Image 2": ModelConfig(
                    base_url="https://api.vectorengine.ai/v1",
                    model_name="gpt-image-2",
                    provider="openai",
                ),
            },
            video_providers={
                "kling": VideoProviderConfig(base_url="https://api.vectorengine.ai"),
                "hailuo": VideoProviderConfig(base_url="https://api.vectorengine.ai"),
                "wanxiang": VideoProviderConfig(base_url="https://api.vectorengine.ai"),
            },
            video_poll=VideoPollConfig(initial_wait=0, interval=0, max_total=60),
        ),
        dingtalk=DingtalkConfig(
            default_table="clothing",
            tables=[
                TableConfig(
                    key="clothing",
                    base_id="tbl_test",
                    sheet_id="sheet_test",
                    image_api_key_env="ZHUOZHI_IMAGE_API_KEY",
                    prompt_field="提示词",
                    model_field="生图模型",
                    reference_image_field="素材图",
                    result_image_field="生成图片",
                    result_status_field="生成结果",
                    result_time_field="生成时间",
                ),
            ],
            video_tables=[
                VideoTableConfig(
                    key="zhuozhi-video",
                    base_id="tbl_video_test",
                    sheet_id="sheet_video_test",
                    video_api_key_env="ZHUOZHI_IMAGE_API_KEY",
                    prompt_field="提示词",
                    video_model_field="视频模型",
                    reference_image_field="首帧图",
                    result_video_field="生成视频",
                    result_status_field="生成结果",
                    result_time_field="生成时间",
                ),
            ],
        ),
    )
    return settings


@pytest.fixture
def mock_settings_with_video_keys(mock_settings, monkeypatch):
    """视频复用同品牌图片 API Key，因此注入的也是 *_IMAGE_API_KEY。"""
    monkeypatch.setenv("ZHUOZHI_IMAGE_API_KEY", "test_video_key")
    monkeypatch.setenv("HUAPU_IMAGE_API_KEY", "test_video_key")
    monkeypatch.setenv("AHMI_IMAGE_API_KEY", "test_video_key")
    return mock_settings


@pytest.fixture
def mock_settings_tight_poll(mock_settings_with_video_keys):
    """轮询超时测试专用：max_total=0 让首次轮询即触发超时。"""
    mock_settings_with_video_keys.ai.video_poll = VideoPollConfig(
        initial_wait=0, interval=0, max_total=0
    )
    return mock_settings_with_video_keys


@pytest.fixture
def app(mock_settings):
    """测试用 FastAPI 应用。

    避免真实初始化 AIGenerator / DingTalkClient（依赖 SDK 环境）。
    """
    from unittest.mock import AsyncMock

    patches = [
        patch("api.deps.get_settings", return_value=mock_settings),
        patch("api.router.get_settings", return_value=mock_settings),
        patch("api.deps.get_ai_generator", return_value=AsyncMock()),
        patch("api.deps.get_dingtalk_client", return_value=AsyncMock()),
        patch("api.deps.get_video_generator", return_value=AsyncMock()),
        patch("api.deps.get_video_generation_service", return_value=AsyncMock()),
    ]
    for p in patches:
        p.start()
    application = create_app()
    yield application
    for p in patches:
        p.stop()


@pytest.fixture
def client(app):
    """测试用 HTTP 客户端。"""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
