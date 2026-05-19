"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from api.router import router
from api.deps import get_settings


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


def create_app() -> FastAPI:
    """创建 FastAPI 应用。"""
    settings = get_settings()

    app = FastAPI(
        title="AI Gen Image",
        description="钉钉AI表格驱动的图片生成后端服务",
        version="0.1.0",
        lifespan=lifespan,
    )

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