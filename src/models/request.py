"""API 请求模型。"""

from pydantic import BaseModel
from typing import Optional


class GenerateRequest(BaseModel):
    """触发生图的请求模型。"""

    record_id: str
    table_key: Optional[str] = None  # 可选，默认使用 config.toml 中的 default_table