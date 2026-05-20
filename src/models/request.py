"""API 请求模型。"""

from pydantic import BaseModel, field_validator


class GenerateRequest(BaseModel):
    """触发生图的请求模型。"""

    record_id: str
    table_key: str | None = None  # 可选，默认使用 config.toml 中的 default_table

    @field_validator("record_id")
    @classmethod
    def record_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("record_id must not be empty")
        return v
