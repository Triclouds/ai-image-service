"""DingTalkClient 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import AiConfig, DingtalkConfig, RetryConfig, ServerConfig, Settings, TableConfig
from dingtalk.client import DingTalkClient


@pytest.fixture
def mock_settings():
    return Settings(
        dingtalk_app_key="test_key",
        dingtalk_app_secret="test_secret",
        dingtalk_operator_id="test_operator",
        api_key="",
        server=ServerConfig(),
        ai=AiConfig(
            default_model="test",
            retry=RetryConfig(initial_delay=1, max_retries=1),
        ),
        dingtalk=DingtalkConfig(tables=[]),
    )


@pytest.fixture
def table_config():
    return TableConfig(
        key="test",
        base_id="tbl_test",
        sheet_id="sheet_test",
    )


@pytest.fixture
def client(mock_settings):
    return DingTalkClient(mock_settings)


def _mock_http(token_response: dict | None = None):
    """Mock httpx.AsyncClient。

    - httpx.AsyncClient() → MagicMock（类级别的 mock）
    - async with 的 client 用 AsyncMock（支持 await client.post(...)）
    - response 用 MagicMock（.json() 是同步方法）
    """
    if token_response is None:
        token_response = {"accessToken": "token_123", "expireIn": 7200}

    mock_class = MagicMock()
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = token_response
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response
    mock_client.put.return_value = mock_response
    mock_class.return_value.__aenter__.return_value = mock_client

    return mock_class, mock_client, mock_response


@pytest.mark.asyncio
async def test_token_caching(client):
    """access_token 应缓存，过期前 5 分钟才刷新。"""
    mock_class, mock_client, _ = _mock_http()

    with patch("httpx.AsyncClient", mock_class):
        token1 = await client._get_access_token()
        assert token1 == "token_123"

        token2 = await client._get_access_token()
        assert token2 == "token_123"

        # post 只应被调用一次（第二次走缓存）
        assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_get_record_calls_sdk(client, table_config):
    """get_record 应调用钉钉 SDK。"""
    mock_class, _, _ = _mock_http()

    with patch("httpx.AsyncClient", mock_class):
        mock_sdk_response = MagicMock()
        mock_sdk_response.body.to_dict.return_value = {"id": "rec_001"}
        client._client.get_record_with_options_async = AsyncMock(return_value=mock_sdk_response)

        record = await client.get_record(table_config, "rec_001")
        assert record == {"id": "rec_001"}
        client._client.get_record_with_options_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_file(client):
    """下载素材图。"""
    mock_class, mock_client, mock_response = _mock_http()
    mock_response.content = b"fake_image_data"

    with patch("httpx.AsyncClient", mock_class):
        data = await client.download_file("/test/file.jpg")
        assert data == b"fake_image_data"
        mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_upload_attachment(client, table_config):
    """上传附件到钉钉云空间。"""
    mock_class, mock_client, mock_token_response = _mock_http()

    # 第二次 post 调用（获取上传信息）返回不同的响应
    upload_response = MagicMock()
    upload_response.json.return_value = {
        "success": True,
        "result": {
            "uploadUrl": "https://oss.example.com/upload",
            "resourceId": "res_001",
            "resourceUrl": "/resource/gen.png",
        },
    }
    # 第一次 post 返回 token，第二次返回 upload info
    mock_client.post.side_effect = [mock_token_response, upload_response]

    with patch("httpx.AsyncClient", mock_class):
        result = await client.upload_attachment(table_config, b"image_bytes", "test.png")

        assert result["resourceId"] == "res_001"
        assert result["url"] == "/resource/gen.png"
        assert result["filename"] == "test.png"
        assert result["size"] == len(b"image_bytes")
        # 验证 PUT 上传到 OSS 被调用
        assert mock_client.put.called


@pytest.mark.asyncio
async def test_retry_on_network_error(client, table_config):
    """网络异常应重试。"""
    mock_class, _, _ = _mock_http()

    with patch("httpx.AsyncClient", mock_class):
        client._client.get_record_with_options_async = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        with pytest.raises(httpx.TimeoutException):
            await client.get_record(table_config, "rec_001")

        # SDK 调用应重试 max_retries + 1 = 2 次
        assert client._client.get_record_with_options_async.await_count == 2
