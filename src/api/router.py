"""API 路由模块。"""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException

from models import GenerateRequest, TaskResponse, HealthResponse
from services.generation import GenerationService
from api.deps import get_generation_service, get_settings

router = APIRouter()


async def verify_api_key(x_api_key: str = Header(..., alias="X-Api-Key")) -> str:
    """验证 API Key。"""
    settings = get_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


@router.post(
    "/api/v1/generate",
    response_model=TaskResponse,
    status_code=202,
    responses={
        202: {"description": "任务已接收，后台处理中"},
        400: {"description": "参数校验失败"},
        401: {"description": "API Key 无效"},
    },
)
async def trigger_generation(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    service: GenerationService = Depends(get_generation_service),
    _api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    """接收钉钉自动化回调，触发异步生图流程。"""
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return TaskResponse(
        status="accepted",
        message="任务已提交，处理完成后将更新表格",
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