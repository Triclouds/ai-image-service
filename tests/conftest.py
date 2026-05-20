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
        server=ServerConfig(host="0.0.0.0", port=8030, log_level="INFO", max_concurrency=5),
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
        ),
        dingtalk=DingtalkConfig(
            default_table="clothing",
            tables=[
                TableConfig(
                    key="clothing",
                    base_id="tbl_test",
                    sheet_id="sheet_test",
                    gpt_image_api_key_env="ZHUOZHI_GPT_IMAGE_API_KEY",
                    nanobanana_api_key_env="ZHUOZHI_NANOBANANA_API_KEY",
                    prompt_field="提示词",
                    model_field="生图模型",
                    reference_image_field="素材图",
                    result_image_field="生成图片",
                    result_status_field="生成结果",
                    result_time_field="生成时间",
                ),
            ],
        ),
    )
    return settings


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
