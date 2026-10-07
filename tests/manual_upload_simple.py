"""简单测试 upload attachment API"""

import asyncio
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")

from alibabacloud_dingtalk.doc_1_0.client import Client as DocClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_dingtalk.doc_1_0 import models as doc_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_dingtalk.oauth2_1_0.client import Client as OAuth2Client
from alibabacloud_dingtalk.oauth2_1_0 import models as oauth2_models
from config import Settings


def create_client():
    config = open_api_models.Config()
    config.protocol = "https"
    config.region_id = "central"
    return DocClient(config), OAuth2Client(config)


async def get_token(settings):
    _, oauth2_client = create_client()
    request = oauth2_models.GetAccessTokenRequest(
        app_key=settings.dingtalk_app_key,
        app_secret=settings.dingtalk_app_secret,
    )
    response = await oauth2_client.get_access_token_async(request)
    return response.body.access_token


async def test_upload_info(doc_id, operator_id, access_token):
    doc_client, _ = create_client()

    headers = doc_models.GetResourceUploadInfoHeaders(
        x_acs_dingtalk_access_token=access_token,
    )
    request = doc_models.GetResourceUploadInfoRequest(
        operator_id=operator_id,
        size=100,
        media_type="image/png",
        resource_name="test.png",
    )

    try:
        response = await doc_client.get_resource_upload_info_with_options_async(
            doc_id, request, headers, util_models.RuntimeOptions()
        )
        if response.body.success:
            print(f"  OK - resourceId: {response.body.result.resource_id}")
        else:
            print(f"  FAIL - success: {response.body.success}")
    except Exception as e:
        code = getattr(e, "code", "unknown")
        msg = getattr(e, "message", str(e))
        print(f"  ERROR - [{code}] {msg}")


async def main():
    settings = Settings()
    token = await get_token(settings)
    operator_id = settings.dingtalk_operator_id

    tables = {
        "zhuozhi-base": settings.get_table("zhuozhi-base").base_id,
        "huapu-base": settings.get_table("huapu-base").base_id,
        "ahmi-base": settings.get_table("ahmi-base").base_id,
    }

    for name, base_id in tables.items():
        print(f"\n{name}:")
        print(f"  base_id: {base_id}")
        await test_upload_info(base_id, operator_id, token)


if __name__ == "__main__":
    asyncio.run(main())
