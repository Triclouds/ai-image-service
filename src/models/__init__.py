"""通用数据模型。"""

from models.prompt_config import PromptConfig, build_prompts
from models.request import GenerateRequest
from models.response import HealthResponse, TaskResponse
from models.video_request import VideoGenerateRequest

__all__ = [
    "GenerateRequest",
    "TaskResponse",
    "HealthResponse",
    "VideoGenerateRequest",
    "PromptConfig",
    "build_prompts",
]
