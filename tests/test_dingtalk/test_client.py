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
        image_api_key_env="TEST_IMAGE_API_KEY",
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
    mock_class, mock_client, _ = _mock_http()

    # Mock Doc SDK 返回上传信息
    from alibabacloud_dingtalk.doc_1_0 import models as doc_models

    mock_response = MagicMock()
    mock_response.body.success = True
    mock_response.body.result.upload_url = "https://oss.example.com/upload"
    mock_response.body.result.resource_id = "res_001"
    mock_response.body.result.resource_url = "/resource/gen.png"
    client._doc_client.get_resource_upload_info_with_options_async = AsyncMock(
        return_value=mock_response
    )

    with patch("httpx.AsyncClient", mock_class):
        result = await client.upload_attachment(table_config, b"image_bytes", "test.png")

        assert result["resourceId"] == "res_001"
        assert result["url"] == "/resource/gen.png"
        assert result["filename"] == "test.png"
        assert result["size"] == len(b"image_bytes")
        # 验证 Doc SDK 被调用
        client._doc_client.get_resource_upload_info_with_options_async.assert_awaited_once()
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


# ─────────── list_records（批量生图跨表查询）───────────


def _patch_token_response(token: str = "token_xyz"):
    """让 _get_access_token 返回固定值，避开真实网络。"""
    async def _fake_token(self):
        return token

    return patch.object(DingTalkClient, "_get_access_token", _fake_token)


@pytest.mark.asyncio
async def test_list_records_single_match(client):
    """list_records 单条匹配：返回 [{id, fields}, ...]。"""
    mock_response = MagicMock()
    mock_response.body.records = [{"id": "rec_001", "fields": {"任务名称": "动作图-A"}}]
    client._client.list_records_with_options_async = AsyncMock(return_value=mock_response)

    with _patch_token_response():
        records = await client.list_records(
            base_id="tbl_test",
            sheet_id="sheet_prompt",
            field="任务名称",
            value="动作图-A",
        )

    assert records == [{"id": "rec_001", "fields": {"任务名称": "动作图-A"}}]
    client._client.list_records_with_options_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_records_no_match(client):
    """list_records 无匹配：返回空列表。"""
    mock_response = MagicMock()
    mock_response.body.records = []
    client._client.list_records_with_options_async = AsyncMock(return_value=mock_response)

    with _patch_token_response():
        records = await client.list_records(
            base_id="tbl_test",
            sheet_id="sheet_prompt",
            field="任务名称",
            value="不存在的任务",
        )

    assert records == []


@pytest.mark.asyncio
async def test_list_records_multiple_matches(client):
    """list_records 多条匹配：按 SDK 返回顺序透传 N 条。"""
    mock_response = MagicMock()
    mock_response.body.records = [
        {"id": "rec_001", "fields": {"任务名称": "动作图-A"}},
        {"id": "rec_002", "fields": {"任务名称": "动作图-A"}},
        {"id": "rec_003", "fields": {"任务名称": "动作图-A"}},
    ]
    client._client.list_records_with_options_async = AsyncMock(return_value=mock_response)

    with _patch_token_response():
        records = await client.list_records(
            base_id="tbl_test",
            sheet_id="sheet_prompt",
            field="任务名称",
            value="动作图-A",
        )

    assert len(records) == 3
    assert [r["id"] for r in records] == ["rec_001", "rec_002", "rec_003"]


@pytest.mark.asyncio
async def test_list_records_network_error_retries(client):
    """list_records 网络异常应触发重试后抛出。"""
    client._client.list_records_with_options_async = AsyncMock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with _patch_token_response():
        with pytest.raises(httpx.TimeoutException):
            await client.list_records(
                base_id="tbl_test",
                sheet_id="sheet_prompt",
                field="任务名称",
                value="动作图-A",
            )

    # max_retries=1 → 调用 1+1=2 次
    assert client._client.list_records_with_options_async.await_count == 2


@pytest.mark.asyncio
async def test_list_records_request_filter_format(client):
    """list_records 参数透传：request.filter 包含 field/operator/value。"""
    mock_response = MagicMock()
    mock_response.body.records = []
    client._client.list_records_with_options_async = AsyncMock(return_value=mock_response)

    with _patch_token_response():
        await client.list_records(
            base_id="tbl_test",
            sheet_id="sheet_prompt",
            field="任务名称",
            value="动作图-A",
            max_results=50,
        )

    # 断言 SDK 调用参数
    call = client._client.list_records_with_options_async.call_args
    assert call.kwargs["base_id"] == "tbl_test"
    assert call.kwargs["sheet_id_or_name"] == "sheet_prompt"
    request = call.kwargs["request"]
    assert request.operator_id == client.operator_id
    assert request.max_results == 50
    assert request.filter["combination"] == "and"
    cond = request.filter["conditions"][0]
    assert cond["field"] == "任务名称"
    assert cond["operator"] == "equal"
    assert cond["value"] == ["动作图-A"]

