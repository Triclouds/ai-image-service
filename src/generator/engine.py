"""AI 生图引擎。

统一入口，按 model 分派到对应 SDK（Google / OpenAI）。
"""

import asyncio
import base64
import io

import httpx
from google import genai
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


class AIGenerator:
    """AI 生图引擎，统一调度 Google / OpenAI SDK。

    根据 config.toml 中 model 配置的 provider 字段（"google" / "openai"）
    分派到对应 SDK。新增模型只需在 config.toml 中添加配置，无需改代码。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def _retry_on_network_error(self, func, *args, **kwargs):
        """网络异常重试装饰器逻辑。"""
        max_retries = self.settings.ai.retry.max_retries
        initial_delay = self.settings.ai.retry.initial_delay
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
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
    ) -> bytes:
        """根据 model 参数分派到对应 SDK，返回统一 PNG 格式字节流。

        Args:
            model: 模型名称（如 "Nano Banana 2"）
            prompt: 生图提示词
            reference_image: 素材图字节（可选）
            table_config: 表格配置，用于获取对应的 API Key 环境变量名。必填，缺失将导致无法获取 API Key。
        """
        if table_config is None:
            raise ValueError("table_config is required to resolve API key")

        async def _do_generate():
            model_cfg = self.settings.get_model(model)
            api_key = self.settings.get_api_key(
                table_config.nanobanana_api_key_env
                if model_cfg.provider == "google"
                else table_config.gpt_image_api_key_env
            )
            logger.info(
                "AI 生图路由",
                model=model,
                provider=model_cfg.provider,
                model_name=model_cfg.model_name,
            )
            if model_cfg.provider == "google":
                raw = await self._generate_nano(
                    model_cfg.base_url, model_cfg.model_name, prompt, reference_image, api_key
                )
            elif model_cfg.provider == "openai":
                raw = await self._generate_gpt(
                    model_cfg.base_url, model_cfg.model_name, prompt, reference_image, api_key
                )
            else:
                raise ValueError(f"Unsupported provider: {model_cfg.provider} (model={model})")
            return _to_png_bytes(raw)

        return await self._retry_on_network_error(_do_generate)

    async def _generate_nano(self, base_url: str, model_name: str, prompt: str, image_bytes: bytes | None, api_key: str) -> bytes:
        """调用 Google genai SDK 图生图。"""
        client = genai.Client(
            api_key=api_key,
            http_options={"base_url": base_url},
        ).aio
        pil_image = Image.open(io.BytesIO(image_bytes))
        response = await client.models.generate_content(
            model=model_name,
            contents=[prompt, pil_image],
        )
        for part in response.parts:
            if part.inline_data is not None:
                return _to_png_bytes(part.inline_data.data)
        raise ValueError("No image generated in response")

    async def _generate_gpt(self, base_url: str, model_name: str, prompt: str, image: bytes | None, api_key: str) -> bytes:
        """调用 OpenAI SDK 图生图。"""
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        response = await client.images.edit(
            model=model_name,
            image=image,
            prompt=prompt,
            n=1,
            response_format="b64_json",
        )
        return base64.b64decode(response.data[0].b64_json)
