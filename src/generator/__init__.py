"""AI 生图引擎模块。

统一入口，按 model 分派到对应 SDK（Google / OpenAI）。
"""

import base64
import io

import httpx
from google import genai
from openai import AsyncOpenAI
from PIL import Image

from config import Settings


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
        self.nano_client = genai.AsyncClient(
            api_key=settings.nanobanana_api_key,
            http_options={"base_url": f"{settings.ai.base_url}/v1beta"},
        )
        self.gpt_client = AsyncOpenAI(
            api_key=settings.gpt_image_api_key,
            base_url=f"{settings.ai.base_url}/v1",
        )

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
                    import asyncio
                    await asyncio.sleep(initial_delay)
        raise last_error

    async def generate(
        self,
        model: str,
        prompt: str,
        reference_image: bytes | None = None,
    ) -> bytes:
        """根据 model 参数分派到对应 SDK，返回统一 PNG 格式字节流。"""

        async def _do_generate():
            model_cfg = self.settings.get_model(model)
            if model_cfg.provider == "google":
                raw = await self._generate_nano(model_cfg.model_name, prompt, reference_image)
            elif model_cfg.provider == "openai":
                raw = await self._generate_gpt(model_cfg.model_name, prompt, reference_image)
            else:
                raise ValueError(f"Unsupported provider: {model_cfg.provider} (model={model})")
            return _to_png_bytes(raw)

        return await self._retry_on_network_error(_do_generate)

    async def _generate_nano(
        self, model_name: str, prompt: str, image_bytes: bytes
    ) -> bytes:
        """调用 Google genai SDK 图生图。"""
        pil_image = Image.open(io.BytesIO(image_bytes))
        response = await self.nano_client.models.generate_content(
            model=model_name,
            contents=[prompt, pil_image],
        )
        for part in response.parts:
            if part.inline_data is not None:
                pil_img = part.as_image()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                return buf.getvalue()
        raise ValueError("No image generated in response")

    async def _generate_gpt(
        self, model_name: str, prompt: str, image: bytes
    ) -> bytes:
        """调用 OpenAI SDK 图生图。"""
        response = await self.gpt_client.images.edit(
            model=model_name,
            image=image,
            prompt=prompt,
            n=1,
            response_format="b64_json",
        )
        return base64.b64decode(response.data[0].b64_json)