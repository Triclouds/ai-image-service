"""端到端真实生图集成测试。

直接调用项目里的 GenerationService.process()，跑完整线上流程：
获取钉钉记录 → 下载素材图 → AI 生图 → 上传结果 → 回写表格。

素材图与成图落盘到 ./images/{date}/{table_key}/{record_id}/（image_dump_enabled=true 时）。

用法:
    cd 项目根目录
    python tests/test_upload_integration.py
"""

import asyncio
import os
import sys
from pathlib import Path

# 确保项目根目录在 CWD 且 .env / config.toml 可被找到
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")

from api.deps import get_generation_service, get_settings

# ============================================================
# 测试目标：ahmi 买家秀表 + 指定记录
# ============================================================
TABLE_KEY = "ahmi-buyerShow"
RECORD_ID = "BaDw28hGeJ"
# ============================================================


async def main():
    settings = get_settings()
    table_config = settings.get_table(TABLE_KEY)
    service = get_generation_service()

    print(f"表名:      {table_config.key}")
    print(f"base_id:   {table_config.base_id}")
    print(f"record_id: {RECORD_ID}")
    print(f"落盘开关:  image_dump_enabled={settings.debug.image_dump_enabled}")
    print("\n开始跑完整生图流程...\n")

    await service.process(RECORD_ID, table_key=TABLE_KEY)
    await service.generator.close()

    print("\n流程结束。参考图/成图见 ./images/ 下对应目录，详细请求体见日志。")


if __name__ == "__main__":
    asyncio.run(main())
