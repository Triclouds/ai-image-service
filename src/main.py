"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError

from api.deps import get_settings
from api.router import router
from utils.exceptions import APIError
from utils.logging import setup_logging
from utils.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    settings = get_settings()
    logger.info(
        "启动服务",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level,
    )
    yield
    logger.info("服务关闭")


def _register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        """Pydantic 校验失败 → 422 统一格式。"""
        errors = _safe_errors(exc.errors())
        logger.warning("请求参数校验失败: {}", errors)
        return JSONResponse(
            status_code=422,
            content={"detail": "请求参数校验失败", "errors": errors},
        )

    @app.exception_handler(APIError)
    async def api_error_handler(request, exc: APIError):
        """自定义 API 错误 → 按 status_code 返回。"""
        logger.warning("API 错误 [{}]: {}", exc.status_code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        """兜底未知错误 → 500。"""
        logger.exception("未捕获异常: {}", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误"},
        )


def _safe_errors(errors: list) -> list:
    """将 errors 中的非 JSON 可序列化对象（如 ctx.error）转为字符串。"""
    _json_types = (str, int, float, bool, type(None))

    def _to_json_safe(val):
        if isinstance(val, _json_types):
            return val
        if isinstance(val, (list, tuple)):
            return [_to_json_safe(i) for i in val]
        if isinstance(val, dict):
            return {k: _to_json_safe(v) for k, v in val.items()}
        return str(val)

    return [_to_json_safe(err) for err in errors]


def create_app() -> FastAPI:
    """创建 FastAPI 应用。"""
    app = FastAPI(
        title="AI Gen Image",
        description="钉钉AI表格驱动的图片生成后端服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 1. 初始化日志配置
    setup_logging(get_settings())

    # 2. 注册请求追踪中间件
    app.add_middleware(RequestContextMiddleware)

    # 3. 注册全局异常处理器
    _register_exception_handlers(app)

    # 4. 注册路由
    app.include_router(router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        reload=False,
    )
