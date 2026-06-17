"""视频生成 API 请求模型。

与 GenerateRequest 字段对称，便于演进时独立调整。
"""

from pydantic import BaseModel, field_validator


class VideoGenerateRequest(BaseModel):
    """触发视频生成的请求模型。

    table_key 必填，因为视频表与图片表完全独立，没有 default 兜底逻辑。
    """

    record_id: str
    table_key: str

    @field_validator("record_id")
    @classmethod
    def record_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("record_id must not be empty")
        return v

    @field_validator("table_key")
    @classmethod
    def table_key_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("table_key must not be empty")
        return v
