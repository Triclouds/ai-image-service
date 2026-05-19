"""API 响应模型。"""

from pydantic import BaseModel


class TaskResponse(BaseModel):
    """任务提交响应模型。"""

    status: str
    message: str
    record_id: str
    timestamp: str


class HealthResponse(BaseModel):
    """健康检查响应模型。"""

    status: str
    version: str = "0.1.0"