"""日志配置。

单次初始化，应用启动时调用一次 setup_logging()。
风格参照 aigenerated_images 项目 main.py 的 setup_logging()。
"""

import sys
from pathlib import Path

from loguru import logger

from config import Settings


def setup_logging(settings: Settings) -> None:
    """配置 loguru 全局日志。

    1. 移除默认 sink
    2. 控制台 sink（带颜色、request_id 上下文）
    3. 文件 sink（按天轮转，保留 7 天）
    4. 错误日志单独文件
    5. 拦截标准 logging 模块（uvicorn / fastapi）
    """
    log_level = settings.server.log_level

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # 设置 extra 默认值，避免非请求日志（如启动日志）中 {extra[request_id]} 抛 KeyError
    logger.configure(patcher=_enrich_record)

    # ── 控制台 ──
    # 使用静态格式字符串，避免 enqueue=True 与 callable format 冲突导致 KeyError
    console_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[request_id]: <8}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stdout,
        format=console_fmt,
        level=log_level,
        colorize=True,
        enqueue=True,
    )

    # ── 文件（全部日志）──
    file_fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{extra[request_id]: <8} | {message}"
    )

    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        format=file_fmt,
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )

    # ── 错误日志单独文件 ──
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        format=file_fmt,
        level="ERROR",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,
    )

    # 拦截标准 logging
    _patch_logging(log_level)

    logger.info("日志系统初始化完成", log_level=log_level, log_dir=str(log_dir))


def _enrich_record(record) -> None:
    """为日志记录补充默认值，并将 extra 字段追加到 message 使其可见。

    所有 logger.info("msg", key=val) 传入的额外参数都位于 record["extra"]
    中，但 loguru 的格式串不会自动渲染它们。这里在格式化前将它们追加到 message 尾部，
    确保控制台和文件日志都能看到这些数据。
    """
    record["extra"].setdefault("request_id", "-")
    extras = {k: v for k, v in record["extra"].items() if k != "request_id"}
    if extras:
        extra_str = " ".join(f"{k}={v}" for k, v in extras.items())
        record["message"] += f" | {extra_str}"


def _patch_logging(level: str) -> None:
    """将标准 logging 模块的日志重定向到 loguru。"""
    import logging

    # 屏蔽 httpx/httpcore 的 DEBUG 日志（HTTP 请求详情太吵）
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")

    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # 跳过已过滤的库
            if record.name.startswith(("httpx", "httpcore")):
                return
            logger_opt = logger.opt(depth=6, exception=record.exc_info)
            logger_opt.log(record.levelno, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=level, force=True)
