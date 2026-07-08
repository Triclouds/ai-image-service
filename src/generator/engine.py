"""AI 生图引擎。

统一入口，按 model 分派到对应 SDK（Google / OpenAI）。
"""

import asyncio
import base64
import io
import random

import httpx

try:
    import aiohttp
    _AIOHTTP_NETWORK_ERRORS = (aiohttp.ClientError,)
except ImportError:
    _AIOHTTP_NETWORK_ERRORS = ()

_NETWORK_RETRY_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
) + _AIOHTTP_NETWORK_ERRORS
from google import genai
from google.genai import types
from loguru import logger
from openai import AsyncOpenAI
from PIL import Image

from config import Settings, TableConfig


def _to_png_bytes(image_bytes: bytes) -> bytes:
    """将任意格式图片字节统一转为 PNG。"""
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _map_gpt_size(resolution: str | None) -> str | None:
    """把 NanoBanana 风格 resolution 映射到 GPT 5 档 size。

    GPT 静态类型只支持 {"256x256", "512x512", "1024x1024", "1536x1024", "1024x1536", "auto"}。
    1K 是唯一能安全命中的档位（不携带 aspect_ratio 信息，GPT 走 prompt 文本理解比例）。
    2K/4K 不在枚举里 → 返回 None，让 GPT 走默认（auto）。
    """
    return "1024x1024" if resolution == "1K" else None


class AIGenerator:
    """AI 生图引擎，统一调度 Google / OpenAI SDK。

    根据 config.toml 中 model 配置的 provider 字段（"google" / "openai"）
    分派到对应 SDK。新增模型只需在 config.toml 中添加配置，无需改代码。
    """

    # 批量生图内部并发上限，避免 count 较大时打爆上游 API rate limit
    _BATCH_CONCURRENCY = 3

    def __init__(self, settings: Settings):
        self.settings = settings
        self._genai_clients: dict[str, genai.Client] = {}

    async def _retry_on_network_error(self, func, *args, **kwargs):
        """网络异常重试装饰器逻辑。"""
        max_retries = self.settings.ai.retry.max_retries
        initial_delay = self.settings.ai.retry.initial_delay
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except _NETWORK_RETRY_ERRORS as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(initial_delay)
        raise last_error

    async def generate(
        self,
        model: str,
        prompt: str,
        reference_image: bytes | None = None,
        table_config: TableConfig | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        """根据 model 参数分派到对应 SDK，返回统一 PNG 格式字节流。

        Args:
            model: 模型名称（如 "Nano Banana 2"）
            prompt: 生图提示词
            reference_image: 素材图字节（可选）
            table_config: 表格配置，通过 image_api_key_env 字段获取 AI 图片 API Key 环境变量名。必填。
            aspect_ratio: 生成比例（如 "16:9"）。仅 NanoBanana 生效，GPT 忽略。
            resolution: 分辨率档位（"1K"/"2K"/"4K"）。仅 NanoBanana 完整生效，
                        GPT 走 _map_gpt_size 最小化映射（仅 1K 命中）。
        """
        if table_config is None:
            raise ValueError("table_config is required to resolve API key")

        async def _do_generate():
            model_cfg = self.settings.get_model(model)
            api_key = self.settings.get_api_key(table_config.image_api_key_env)
            logger.info(
                "AI 生图路由",
                model=model,
                provider=model_cfg.provider,
                model_name=model_cfg.model_name,
                aspect_ratio=aspect_ratio or "",
                resolution=resolution or "",
            )
            if model_cfg.provider == "google":
                raw = await self._generate_nano(
                    model_cfg.base_url, model_cfg.model_name,
                    prompt, reference_image, api_key,
                    aspect_ratio=aspect_ratio, resolution=resolution,
                )
            elif model_cfg.provider == "openai":
                raw = await self._generate_gpt(
                    model_cfg.base_url, model_cfg.model_name,
                    prompt, reference_image, api_key,
                    resolution=resolution,
                )
            else:
                raise ValueError(f"Unsupported provider: {model_cfg.provider} (model={model})")
            return raw
        return await self._retry_on_network_error(_do_generate)

    async def generate_batch(
        self,
        model: str,
        prompts: list[str],
        reference_image: bytes | None = None,
        table_config: TableConfig | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> list[bytes | None]:
        """并发调 generate() 生成多张图，单张失败转 None 不抛。

        API Key 通过 table_config.image_api_key_env 解析。
        内部用 Semaphore 限制并发数（_BATCH_CONCURRENCY）。
        aspect_ratio / resolution 对每张图相同，透传到 generate()。
        单张传输层异常重试 1 次（退避 1s），两次均失败则转为 None。
        """
        if not prompts:
            return []

        semaphore = asyncio.Semaphore(self._BATCH_CONCURRENCY)

        async def _one(idx: int, prompt: str) -> bytes:
            last_error = None
            for attempt in range(2):  # 首次 + 1 次重试
                try:
                    async with semaphore:
                        return await self.generate(
                            model=model, prompt=prompt,
                            reference_image=reference_image,
                            table_config=table_config,
                            aspect_ratio=aspect_ratio,
                            resolution=resolution,
                        )
                except _NETWORK_RETRY_ERRORS as e:
                    last_error = e
                    if attempt == 0:
                        logger.warning(
                            "单图生成失败，1s后重试",
                            index=idx, error=str(e),
                        )
                        await asyncio.sleep(1.0)
            # 重试耗尽，抛出完整异常
            raise last_error  # type: ignore[misc]

        results = await asyncio.gather(
            *[_one(i, p) for i, p in enumerate(prompts)],
            return_exceptions=True,
        )
        processed: list[bytes | None] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.warning("单图生成失败", index=i, error=str(r))
                processed.append(None)
            else:
                processed.append(r)
        return processed

    async def _generate_nano(
        self,
        base_url: str,
        model_name: str,
        prompt: str,
        image_bytes: bytes | None,
        api_key: str,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> bytes:
        """调用 Google genai SDK 图生图。

        aspect_ratio / resolution 任一非空时构造 image_config，否则 config=None 走 SDK 默认。
        """
        client = self._get_genai_client(api_key, base_url).aio
        pil_image = Image.open(io.BytesIO(image_bytes))

        # 仅在用户提供比例/分辨率时才加 image_config，避免空 ImageConfig() 触发 SDK 校验
        config = None
        if aspect_ratio or resolution:
            config = types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio or None,
                    image_size=resolution or None,
                ),
            )

        response = await client.models.generate_content(
            model=model_name,
            contents=[prompt, pil_image],
            config=config,
        )
        for part in response.parts:
            if part.inline_data is not None:
                return _to_png_bytes(part.inline_data.data)
        raise ValueError("No image generated in response")

    async def _generate_gpt(
        self,
        base_url: str,
        model_name: str,
        prompt: str,
        image: bytes | None,
        api_key: str,
        resolution: str | None = None,
    ) -> bytes:
        """调用 OpenAI SDK 图生图。

        resolution 走 _map_gpt_size 映射到 5 档 size，映射不上为 None（走 SDK 默认 auto）。
        aspect_ratio 对 GPT 无效，忽略。
        """
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=30.0),
            max_retries=0,
        )
        size = _map_gpt_size(resolution)
        response = await client.images.edit(
            model=model_name,
            image=image,
            prompt=prompt,
            n=1,
            response_format="b64_json",
            size=size,
        )
        return base64.b64decode(response.data[0].b64_json)

    def _get_genai_client(self, api_key: str, base_url: str) -> genai.Client:
        """按 base_url + api_key 前缀缓存 Client，复用连接池。"""
        cache_key = f"{base_url}:{api_key[:8]}"
        if cache_key not in self._genai_clients:
            self._genai_clients[cache_key] = genai.Client(
                api_key=api_key,
                http_options={"base_url": base_url},
            )
        return self._genai_clients[cache_key]

    async def close(self) -> None:
        """关闭所有缓存的 genai Client，释放底层连接池。"""
        for client in self._genai_clients.values():
            await client.aio.close()
        self._genai_clients.clear()
