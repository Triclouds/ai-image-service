"""钉钉 API 客户端。

封装钉钉 SDK 调用，管理 Token 缓存，支持多表格场景。
"""

import time
from typing import Optional

import httpx
from alibabacloud_dingtalk.notable_1_0.client import Client as NotableClient
from alibabacloud_dingtalk.notable_1_0 import models as notable_models
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

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
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

        config = open_api_models.Config()
        config.protocol = "https"
        config.region_id = "central"
        self._client = NotableClient(config)

    async def _get_access_token(self) -> str:
        """获取 access_token，带缓存，过期前 5 分钟主动刷新。"""
        if self._token and time.time() < self._token_expires_at - 300:
            return self._token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": self.app_key, "appSecret": self.app_secret},
                headers={"Content-Type": "application/json"},
            )
            result = response.json()
            self._token = result["accessToken"]
            self._token_expires_at = time.time() + result.get("expireIn", 7200)
            return self._token

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
                    import asyncio
                    await asyncio.sleep(initial_delay)
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
            return response.body.to_dict() if hasattr(response, "body") else {}

        return await self._retry_on_network_error(_do_get)

    async def update_record(
        self, table_config: TableConfig, record_id: str, fields: dict
    ) -> None:
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
        self, table_config: TableConfig, image_bytes: bytes, filename: str
    ) -> dict:
        """上传附件到钉钉云空间，返回附件格式数据。"""
        file_size = len(image_bytes)
        media_type = "image/png"

        async def _do_upload():
            token = await self._get_access_token()

            # Step 1: 获取上传信息
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.dingtalk.com/v1.0/doc/docs/resources/{table_config.base_id}/uploadInfos/query",
                    json={
                        "size": file_size,
                        "mediaType": media_type,
                        "resourceName": filename,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "x-acs-dingtalk-access-token": token,
                    },
                    params={"operatorId": self.operator_id},
                )
                upload_info = resp.json()

                if not upload_info.get("success"):
                    raise Exception(f"获取上传信息失败: {upload_info}")

                result = upload_info["result"]
                upload_url = result["uploadUrl"]
                resource_id = result["resourceId"]
                resource_url = result["resourceUrl"]

            # Step 2: PUT 上传文件到 OSS
            async with httpx.AsyncClient() as client:
                await client.put(
                    upload_url,
                    content=image_bytes,
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
            full_url = f"https://api.dingtalk.com{file_url}"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    full_url,
                    headers={"x-acs-dingtalk-access-token": token},
                )
                response.raise_for_status()
                return response.content

        return await self._retry_on_network_error(_do_download)