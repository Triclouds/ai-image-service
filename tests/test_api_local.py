"""本地 API 调用测试脚本。

使用方法:
1. 确保 API 服务已启动 (uvicorn src.main:app --reload)
2. 修改脚本中的 API_KEY 和参数
3. 运行: python tests/test_api_local.py
"""

import asyncio
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
load_dotenv(dotenv_path=Path("configs/.env"))

# 配置
BASE_URL = "http://localhost:8030"
API_KEY = os.getenv("API_KEY", "your-api-key")


async def test_health():
    """测试健康检查接口。"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{BASE_URL}/api/v1/health")
        print(f"GET /api/v1/health -> {response.status_code}")
        print(f"Response: {response.json()}")
        print()


async def test_generate(record_id: str, table_key: str | None = None):
    """测试生图接口。"""
    async with httpx.AsyncClient() as client:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "record_id": record_id,
        }
        if table_key:
            payload["table_key"] = table_key

        print(f"POST /api/v1/generate")
        print(f"Headers: {headers}")
        print(f"Payload: {payload}")

        try:
            response = await client.post(
                f"{BASE_URL}/api/v1/generate",
                headers=headers,
                json=payload,
                timeout=10.0,
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")
        except httpx.HTTPStatusError as e:
            print(f"HTTP 错误: {e.response.status_code}")
            print(f"Response: {e.response.text}")
        except httpx.TimeoutException:
            print("请求超时")
        except Exception as e:
            print(f"错误: {e}")
        print()

async def main():

    # 测试生图接口 (需要真实的 record_id)
    print("【测试 4】生图接口 (需要有效 record_id)")
    print("-" * 50)
    # 替换为钉钉表格中的真实 record_id
    await test_generate(record_id="1TbkB1VbMl", table_key="zhuozhi-base")


if __name__ == "__main__":
    import os

    asyncio.run(main())