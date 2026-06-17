"""依赖注入模块。"""

from functools import lru_cache

from config import Settings
from dingtalk.client import DingTalkClient
from generator import AIGenerator
from generator.video_engine import VideoGenerator
from services.generation import GenerationService
from services.video_generation import VideoGenerationService


@lru_cache
def get_settings() -> Settings:
    """Settings 只加载一次，全局共享。"""
    return Settings()


@lru_cache
def get_dingtalk_client() -> DingTalkClient:
    """钉钉客户端只创建一次，Token 缓存一直有效。"""
    settings = get_settings()
    return DingTalkClient(settings)


@lru_cache
def get_ai_generator() -> AIGenerator:
    """AI 引擎只创建一次，SDK Client 复用。"""
    settings = get_settings()
    return AIGenerator(settings)


@lru_cache
def get_video_generator() -> VideoGenerator:
    """视频引擎只创建一次，复用内部 HTTP 客户端配置。"""
    settings = get_settings()
    return VideoGenerator(settings)


@lru_cache
def get_generation_service() -> GenerationService:
    """组合以上所有依赖。"""
    return GenerationService(
        dingtalk=get_dingtalk_client(),
        generator=get_ai_generator(),
        settings=get_settings(),
    )


@lru_cache
def get_video_generation_service() -> VideoGenerationService:
    """视频编排服务：与图片 Service 完全独立的依赖组合。

    复用同一个 DingTalkClient（共享 access_token 缓存），
    但使用独立的 VideoGenerator 与独立的 Semaphore。
    """
    return VideoGenerationService(
        dingtalk=get_dingtalk_client(),
        video_generator=get_video_generator(),
        settings=get_settings(),
    )
