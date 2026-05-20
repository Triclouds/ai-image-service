"""FastAPI 中间件。"""

import uuid

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求注入 request_id 到 loguru 上下文。

    所有在此中间件之后的日志自动携带 request_id。
    后台任务开始时需手动 logger.bind(request_id=...) 延续链路。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4())[:8])
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response
