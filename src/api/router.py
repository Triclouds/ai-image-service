"""API 路由模块。"""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from loguru import logger

from api.deps import get_generation_service, get_settings, get_video_generation_service
from models import GenerateRequest, HealthResponse, TaskResponse, VideoGenerateRequest
from services.generation import GenerationService
from services.video_generation import VideoGenerationService

router = APIRouter()


async def verify_api_key(authorization: str | None = Header(None, alias="Authorization")) -> str:
    """验证 API Key。"""
    settings = get_settings()
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid API key")
    api_key = authorization.removeprefix("Bearer ")
    if api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


@router.post(
    "/api/v1/generate",
    response_model=TaskResponse,
    status_code=200,
    responses={
        200: {"description": "任务已接收，后台处理中"},
        400: {"description": "参数校验失败"},
        401: {"description": "API Key 无效"},
    },
)
async def trigger_generation(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    service: GenerationService = Depends(get_generation_service),  # noqa: B008
    _api_key: str = Depends(verify_api_key),  # noqa: B008
) -> TaskResponse:
    """接收钉钉自动化回调，触发异步生图流程。"""
    logger.info("收到 generate 请求", record_id=req.record_id, table_key=req.table_key)
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return TaskResponse(
        status="accepted",
        message="任务已提交，处理完成后将更新表格",
        record_id=req.record_id,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@router.post(
    "/api/v1/video/generate",
    response_model=TaskResponse,
    status_code=200,
    responses={
        200: {"description": "视频生成任务已接收，后台处理中"},
        400: {"description": "参数校验失败"},
        401: {"description": "API Key 无效"},
        404: {"description": "视频表格配置未找到"},
    },
)
async def trigger_video_generation(
    req: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    service: VideoGenerationService = Depends(get_video_generation_service),  # noqa: B008
    _api_key: str = Depends(verify_api_key),  # noqa: B008
) -> TaskResponse:
    """接收钉钉自动化回调，触发异步视频生成流程（提交 + 轮询 + 上传 + 回写）。"""
    logger.info("收到 video generate 请求", record_id=req.record_id, table_key=req.table_key)
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return TaskResponse(
        status="accepted",
        message="视频生成任务已提交，处理完成后将更新表格",
        record_id=req.record_id,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@router.get(
    "/api/v1/health",
    response_model=HealthResponse,
    responses={200: {"description": "服务正常"}},
)
async def health_check() -> HealthResponse:
    """健康检查接口。"""
    return HealthResponse(status="ok", version="0.1.0")
