"""pytest 配置。"""

import pytest
from unittest.mock import patch

from config import Settings, ServerConfig, AiConfig, RetryConfig, DingtalkConfig, TableConfig, ModelConfig
from main import create_app


@pytest.fixture
def mock_settings():
    """测试用配置。"""
    settings = Settings(
        dingtalk_app_key="test_app_key",
        dingtalk_app_secret="test_app_secret",
        dingtalk_operator_id="test_operator_id",
        api_key="test_api_key",
        nanobanana_api_key="test_nanobanana_key",
        gpt_image_api_key="test_gpt_key",
        server=ServerConfig(host="0.0.0.0", port=8030, log_level="INFO", max_concurrency=5),
        ai=AiConfig(
            default_model="Nano Banana 2",
            base_url="https://api.vectorengine.ai",
            retry=RetryConfig(initial_delay=1, max_retries=1),
            models={
                "Nano Banana Pro": ModelConfig(
                    endpoint="/v1beta/models/gemini-3-pro-image-preview:generateContent",
                    model_name="gemini-3-pro-image-preview",
                    provider="google",
                ),
                "Nano Banana 2": ModelConfig(
                    endpoint="/v1beta/models/gemini-3.1-flash-image-preview:generateContent",
                    model_name="gemini-3.1-flash-image-preview",
                    provider="google",
                ),
                "GPT Image 2": ModelConfig(
                    endpoint="/v1/images/edits",
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
    """测试用 FastAPI 应用。"""
    with patch("api.deps.get_settings", return_value=mock_settings):
        application = create_app()
        yield application


@pytest.fixture
def client(app):
    """测试用 HTTP 客户端。"""
    from httpx import AsyncClient
    return AsyncClient(app=app, base_url="http://test")