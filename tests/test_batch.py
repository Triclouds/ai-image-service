#!/usr/bin/env python3
"""批量生图接口冒烟测试。

直接 python tests/test_batch.py 即可。先确认服务在 8030 端口跑着，
且 .env / config.toml 里都有 zhuozhi-batch-action 配置。

成功后：去钉钉表格看那条记录，附件字段会出现多张图。
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# 与主应用一致的 .env 加载顺序：先 ./env 再 configs/.env，已有 env 不覆盖
load_dotenv(dotenv_path=Path(".env"), override=False)
load_dotenv(dotenv_path=Path("configs/.env"), override=False)


def main() -> int:
    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        print("[失败] API_KEY 未配置。请在 .env 或 configs/.env 里设 API_KEY=xxx")
        return 1

    url = os.getenv("TEST_URL", "http://localhost:8030/api/v1/generate")
    record_id = os.getenv("RECORD_ID", "JGoMqsastP")
    table_key = os.getenv("TABLE_KEY", "ahmi-batch-action")
    timeout = 10

    print(f"[请求] POST {url}")
    print(f"[参数] record_id={record_id}  table_key={table_key}")

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"record_id": record_id, "table_key": table_key},
            timeout=timeout,
        )
    except requests.RequestException as e:
        print(f"[失败] 网络错误: {e}")
        return 1

    print(f"[响应] HTTP {resp.status_code}")
    print(f"[Body] {resp.text}")

    if resp.status_code == 200 and resp.json().get("status") == "accepted":
        print("[OK] 服务已接收任务，去钉钉表格观察生成结果")
        print("      一般 30s~2min 完成（取决于生成数量和上游 API 速度）")
        return 0

    print("[FAIL] 服务未正常接收任务")
    return 1


if __name__ == "__main__":
    sys.exit(main())