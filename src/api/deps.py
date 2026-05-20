"""依赖注入模块。"""

from functools import lru_cache

from config import Settings
from dingtalk.client import DingTalkClient
from generator import AIGenerator
from services.generation import GenerationService


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
def get_generation_service() -> GenerationService:
    """组合以上所有依赖。"""
    return GenerationService(
        dingtalk=get_dingtalk_client(),
        generator=get_ai_generator(),
        settings=get_settings(),
    )
