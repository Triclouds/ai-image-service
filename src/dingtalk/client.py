"""钉钉 API 客户端。

封装钉钉 SDK 调用，管理 Token 缓存，支持多表格场景。
"""

import asyncio
import time
import traceback
from urllib.parse import urljoin

import httpx
from alibabacloud_dingtalk.notable_1_0 import models as notable_models
from alibabacloud_dingtalk.notable_1_0.client import Client as NotableClient
from alibabacloud_dingtalk.oauth2_1_0 import models as oauth2_models
from alibabacloud_dingtalk.oauth2_1_0.client import Client as OAuth2Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_dingtalk.doc_1_0.client import Client as DocClient
from alibabacloud_dingtalk.doc_1_0 import models as doc_models
from darabonba.policy.retry import RetryOptions, RetryCondition
from loguru import logger

from config import Settings, TableConfig


class DingTalkClient:
    """钉钉 OpenAPI 客户端。

    初始化只绑定全局共享参数（app_key、app_secret、operator_id）。
    base_id 和 sheet_id 在方法级别通过 TableConfig 传入，支持多表格场景。
    """

    def __init__(self, settings: Settings):
        self.app_key = settings.dingtalk_app_key
        self.app_secret = settings.dingtalk_app_secret
        self.operator_id = settings.dingtalk_operator_id
        self.settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0

        config = open_api_models.Config(
            protocol="https",
            region_id="central",
            connect_timeout=10_000,   # 10 秒（单位是**毫秒**）
            read_timeout=60_000,      # 60 秒（单位是**毫秒**）
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
        self._client = NotableClient(config)
        self._oauth2_client = OAuth2Client(config)
        self._doc_client = DocClient(config)

    def _masked_app_key(self) -> str:
        """脱敏 app_key，前6位+****+后2位。"""
        key = self.app_key or ""
        if len(key) <= 8:
            return key[:4] + "****" if key else "(empty)"
        return key[:6] + "****" + key[-2:]

    async def _get_access_token(self) -> str:
        """获取 access_token，带缓存，过期前 5 分钟主动刷新。

        使用 alibabacloud_dingtalk.oauth2_1_0 SDK 获取 token，
        异常时 err.code / err.message 提供详细失败原因。
        """
        token = self._token
        if token and time.time() < self._token_expires_at - 300:
            return token

        masked_key = self._masked_app_key()
        logger.info(f"钉钉 Token 过期或不存在，开始刷新 app_key={masked_key}")

        request = oauth2_models.GetAccessTokenRequest(
            app_key=self.app_key,
            app_secret=self.app_secret,
        )
        try:
            response = await self._oauth2_client.get_access_token_async(request)
            self._token = response.body.access_token
            self._token_expires_at = time.time() + response.body.expire_in
            logger.info(f"钉钉 Token 刷新成功 app_key={masked_key} expires_in={response.body.expire_in}")
            return self._token
        except Exception as e:
            code = getattr(e, "code", "unknown")
            message = getattr(e, "message", str(e))
            tb = traceback.format_exc()
            logger.error(f"钉钉 Token 刷新失败: [{code}] {message}\n{tb}")
            raise RuntimeError(
                f"钉钉 Token 刷新失败: [{code}] {message}"
            ) from e

    def _headers(self, token: str) -> notable_models.GetRecordHeaders:
        """构造带 token 的请求头。"""
        headers = notable_models.GetRecordHeaders()
        headers.x_acs_dingtalk_access_token = token
        return headers

    async def _retry_on_network_error(self, func, *args, **kwargs):
        """网络异常重试装饰器逻辑。"""
        max_retries = self.settings.ai.retry.max_retries
        initial_delay = self.settings.ai.retry.initial_delay
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(initial_delay)
        # last_error 一定不为 None，循环只有抛出异常时才会到此处
        assert last_error is not None
        raise last_error

    async def get_record(self, table_config: TableConfig, record_id: str) -> dict:
        """获取表格记录。"""

        async def _do_get():
            token = await self._get_access_token()
            request = notable_models.GetRecordRequest(operator_id=self.operator_id)
            response = await self._client.get_record_with_options_async(
                base_id=table_config.base_id,
                sheet_id_or_name=table_config.sheet_id,
                record_id=record_id,
                request=request,
                headers=self._headers(token),
                runtime=util_models.RuntimeOptions(),
            )
            return response.body.to_map() if hasattr(response, "body") else {}

        return await self._retry_on_network_error(_do_get)

    async def update_record(self, table_config: TableConfig, record_id: str, fields: dict) -> None:
        """更新表格记录。"""

        async def _do_update():
            token = await self._get_access_token()
            records = [
                notable_models.UpdateRecordsRequestRecords(
                    id=record_id,
                    fields=fields,
                )
            ]
            request = notable_models.UpdateRecordsRequest(
                operator_id=self.operator_id,
                records=records,
            )
            await self._client.update_records_with_options_async(
                base_id=table_config.base_id,
                sheet_id_or_name=table_config.sheet_id,
                request=request,
                headers=self._headers(token),
                runtime=util_models.RuntimeOptions(),
            )

        await self._retry_on_network_error(_do_update)

    async def upload_attachment(
        self,
        table_config: TableConfig,
        file_bytes: bytes,
        filename: str,
        media_type: str = "image/png",
    ) -> dict:
        """上传附件到钉钉云空间，返回附件格式数据。

        Args:
            table_config: 表格配置。
            file_bytes: 任意类型文件字节（图生图场景用 "image/png"，图生视频场景用 "video/mp4"）。
            filename: 文件名（需带扩展名）。
            media_type: MIME 类型，默认 "image/png"。
        """
        file_size = len(file_bytes)

        async def _do_upload():
            token = await self._get_access_token()

            # Step 1: 获取上传信息（使用 Doc SDK）
            headers = doc_models.GetResourceUploadInfoHeaders(
                x_acs_dingtalk_access_token=token,
            )
            request = doc_models.GetResourceUploadInfoRequest(
                operator_id=self.operator_id,
                size=file_size,
                media_type=media_type,
                resource_name=filename,
            )
            response = await self._doc_client.get_resource_upload_info_with_options_async(
                table_config.base_id,
                request,
                headers,
                util_models.RuntimeOptions(),
            )
            body = response.body

            if not body.success:
                raise Exception(f"获取上传信息失败: success={body.success}")

            result = body.result
            upload_url = result.upload_url
            resource_id = result.resource_id
            resource_url = result.resource_url

            # Step 2: PUT 上传文件到 OSS
            async with httpx.AsyncClient(timeout=60) as client:
                await client.put(
                    upload_url,
                    content=file_bytes,
                    headers={"Content-Type": media_type},
                )

            return {
                "filename": filename,
                "size": file_size,
                "type": media_type,
                "url": resource_url,
                "resourceId": resource_id,
            }

        return await self._retry_on_network_error(_do_upload)

    async def download_file(self, file_url: str) -> bytes:
        """下载素材图。"""

        async def _do_download():
            token = await self._get_access_token()
            full_url = urljoin("https://api.dingtalk.com", file_url)

            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    full_url,
                    headers={"x-acs-dingtalk-access-token": token},
                )
                response.raise_for_status()
                return response.content

        return await self._retry_on_network_error(_do_download)

    async def list_records(
        self,
        base_id: str,
        sheet_id: str,
        field: str,
        value: str,
        field_names: list[str] | None = None,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> list[dict]:
        """按字段精确过滤查询。

        对应钉钉 SDK: list_records_with_options_async
        详见 docs/sdk_docs/列出多行记录.md

        Returns:
            [{id, fields, ...}, ...]；空列表 = 未找到。
        """
        async def _do():
            token = await self._get_access_token()
            headers = notable_models.ListRecordsHeaders()
            headers.x_acs_dingtalk_access_token = token

            request = notable_models.ListRecordsRequest(
                operator_id=self.operator_id,
                max_results=max_results,
                next_token=next_token,
                filter=notable_models.ListRecordsRequestFilter(
                    combination="and",
                    conditions=[notable_models.ListRecordsRequestFilterConditions(
                        field=field,
                        operator="equal",
                        value=[value],
                    )],
                ),
                field_id_or_names=field_names,
            )
            response = await self._client.list_records_with_options_async(
                base_id=base_id,
                sheet_id_or_name=sheet_id,
                request=request,
                headers=headers,
                runtime=util_models.RuntimeOptions(),
            )
            return response.body.records if hasattr(response, "body") else []

        return await self._retry_on_network_error(_do)
