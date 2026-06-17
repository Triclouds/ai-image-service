"""视频生成统一引擎。

完全独立于 AIGenerator（不复用 google/openai SDK 路由）。
按 model_name 前缀解析到三家厂商（Kling / Hailuo / Wanxiang），
每家都是异步任务模式：提交 → 轮询 → 下载视频字节。

model_name 由钉钉表格"视频模型"字段直接透传，服务侧零翻译。
首帧图统一转换为 PNG 后 base64 编码再提交，三家中转站均支持 base64。
"""

import asyncio
import base64
import time

import httpx
from loguru import logger

from config import Settings, VideoProviderConfig, VideoTableConfig
from generator.engine import _to_png_bytes

# 终态映射：不同厂商的成功 / 失败状态字符串归一（统一小写比较）
_TERMINAL_SUCCESS = {"succeed", "success", "succeeded"}
_TERMINAL_FAILURE = {"failed", "fail"}


def _resolve_provider(model_name: str) -> str:
    """根据 model_name 前缀路由到厂商标识。

    新增厂商只需在此加一个前缀判断 + 在 VideoGenerator 加 _submit_x/_poll_x 方法。
    """
    name = model_name.strip().lower()
    if name.startswith("kling"):
        return "kling"
    if name.startswith(("minimax-hailuo", "minimax", "hailuo")):
        return "hailuo"
    if name.startswith(("happyhorse", "wanx", "wan")):
        return "wanxiang"
    raise ValueError(f"Unknown video model (cannot route to provider): {model_name}")


class VideoGenerator:
    """视频生成统一引擎：提交任务 + 轮询 + 下载结果。

    内部对每个 HTTP 调用统一走 _retry_on_network_error 模式（与
    AIGenerator / DingTalkClient 同模式，独立维护避免耦合）。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def _retry_on_network_error(self, func, *args, **kwargs):
        """网络异常重试：max_retries 次，初始延迟 initial_delay 秒。"""
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
        assert last_error is not None
        raise last_error

    async def generate(
        self,
        model: str,
        prompt: str,
        reference_image: bytes,
        table_config: VideoTableConfig,
    ) -> bytes:
        """提交视频任务 → 轮询 → 下载视频字节。

        Args:
            model: 钉钉表格原值（如 "kling-v2-5-turbo"），直接作为 API 的 model_name。
            prompt: 提示词。
            reference_image: 首帧图原始字节（任意格式，内部转 PNG 后 base64）。
            table_config: 视频表格配置，通过 video_api_key_env 取 API Key。

        Returns:
            mp4 视频字节。
        """
        provider = _resolve_provider(model)
        provider_cfg = self.settings.get_video_provider(provider)
        api_key = self.settings.get_api_key(table_config.video_api_key_env)
        image_b64 = base64.b64encode(_to_png_bytes(reference_image)).decode("ascii")

        logger.info(
            "视频生成路由",
            model=model,
            provider=provider,
            table_key=table_config.key,
        )

        if provider == "kling":
            task_id = await self._submit_kling(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_kling(provider_cfg, task_id, api_key)
        elif provider == "hailuo":
            task_id = await self._submit_hailuo(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_hailuo(provider_cfg, task_id, api_key)
        elif provider == "wanxiang":
            task_id = await self._submit_wanxiang(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_wanxiang(provider_cfg, task_id, api_key)
        else:
            raise ValueError(f"Unsupported video provider: {provider}")

        return await self._download_video(video_url)

    # ─────────── 通用下载 ───────────

    async def _download_video(self, video_url: str) -> bytes:
        """下载生成的 mp4 视频字节。"""

        async def _do_download():
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.get(video_url)
                response.raise_for_status()
                return response.content

        return await self._retry_on_network_error(_do_download)

    # ─────────── Kling ───────────

    async def _submit_kling(
        self,
        cfg: VideoProviderConfig,
        model: str,
        prompt: str,
        image_b64: str,
        api_key: str,
    ) -> str:
        """Kling 提交任务，返回 task_id。"""

        async def _do_submit():
            url = f"{cfg.base_url.rstrip('/')}/kling/v1/videos/image2video"
            payload = {
                "model_name": model,
                "image": image_b64,
                "prompt": prompt,
                "negative_prompt": "",
                "duration": "5",
                "aspect_ratio": "16:9",
                "cfg_scale": 0.5,
                "mode": "std",
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Kling submit failed: {data}")
            return data["data"]["task_id"]

        return await self._retry_on_network_error(_do_submit)

    async def _poll_kling(
        self,
        cfg: VideoProviderConfig,
        task_id: str,
        api_key: str,
    ) -> str:
        """Kling 轮询直至 succeed/failed，返回视频 URL。"""
        url = f"{cfg.base_url.rstrip('/')}/kling/v1/videos/image2video/{task_id}"
        return await self._poll_until_done(
            url=url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            extract_status=lambda d: d.get("data", {}).get("task_status", ""),
            extract_url=lambda d: d["data"]["task_result"]["videos"][0]["url"],
            provider_name="kling",
        )

    # ─────────── Hailuo ───────────

    async def _submit_hailuo(
        self,
        cfg: VideoProviderConfig,
        model: str,
        prompt: str,
        image_b64: str,
        api_key: str,
    ) -> str:
        """Hailuo 提交任务，返回 task_id（在响应顶层）。"""

        async def _do_submit():
            url = f"{cfg.base_url.rstrip('/')}/minimax/v1/video_generation"
            payload = {
                "model": model,
                "prompt": prompt,
                "duration": 10,
                "first_frame_image": image_b64,
                "resolution": "768P",
                "prompt_optimizer": True,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                raise RuntimeError(f"Hailuo submit failed: {data}")
            return data["task_id"]

        return await self._retry_on_network_error(_do_submit)

    async def _poll_hailuo(
        self,
        cfg: VideoProviderConfig,
        task_id: str,
        api_key: str,
    ) -> str:
        """Hailuo 轮询直至 Success/Fail，返回视频 URL。"""
        url = f"{cfg.base_url.rstrip('/')}/minimax/v1/query/video_generation"
        return await self._poll_until_done(
            url=url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            params={"task_id": task_id},
            extract_status=lambda d: d.get("data", {}).get("status", ""),
            extract_url=lambda d: d["data"]["file"]["download_url"],
            provider_name="hailuo",
        )

    # ─────────── Wanxiang ───────────

    async def _submit_wanxiang(
        self,
        cfg: VideoProviderConfig,
        model: str,
        prompt: str,
        image_b64: str,
        api_key: str,
    ) -> str:
        """Wanxiang 提交任务，返回 task_id（在 output.task_id）。"""

        async def _do_submit():
            base = cfg.base_url.rstrip("/")
            url = f"{base}/alibailian/api/v1/services/aigc/video-generation/video-synthesis"
            payload = {
                "model": model,
                "input": {
                    "prompt": prompt,
                    "media": [{"type": "first_frame", "url": image_b64}],
                },
                "parameters": {"resolution": "720P", "duration": 5},
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            output = data.get("output", {})
            if not output.get("task_id"):
                raise RuntimeError(f"Wanxiang submit failed: {data}")
            return output["task_id"]

        return await self._retry_on_network_error(_do_submit)

    async def _poll_wanxiang(
        self,
        cfg: VideoProviderConfig,
        task_id: str,
        api_key: str,
    ) -> str:
        """Wanxiang 轮询直至 SUCCEEDED/FAILED，返回视频 URL。"""
        url = f"{cfg.base_url.rstrip('/')}/alibailian/api/v1/tasks/{task_id}"
        return await self._poll_until_done(
            url=url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            extract_status=lambda d: d.get("output", {}).get("task_status", ""),
            extract_url=lambda d: d["output"]["video_url"],
            provider_name="wanxiang",
        )

    # ─────────── 统一轮询骨架 ───────────

    async def _poll_until_done(
        self,
        url: str,
        headers: dict,
        extract_status,
        extract_url,
        provider_name: str,
        params: dict | None = None,
    ) -> str:
        """三家轮询共用骨架。

        策略：先 initial_wait 秒，此后每 interval 秒一次，总上限 max_total 秒。
        终态：extract_status 返回 succeed/Success/SUCCEEDED → 取 URL；
              提取到 failed/Fail/FAILED → 抛错。
        """
        poll = self.settings.ai.video_poll
        await asyncio.sleep(poll.initial_wait)

        deadline = time.monotonic() + poll.max_total
        attempt = 0
        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                attempt += 1
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                status = extract_status(data)
                status_lower = status.lower()

                if status_lower in _TERMINAL_SUCCESS:
                    video_url = extract_url(data)
                    logger.info(
                        f"{provider_name} 视频任务完成",
                        attempt=attempt,
                        status=status,
                    )
                    return video_url
                if status_lower in _TERMINAL_FAILURE:
                    raise RuntimeError(f"{provider_name} video task failed: {data}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{provider_name} video task timeout after {poll.max_total}s "
                        f"(last status={status}, attempt={attempt})"
                    )

                logger.info(
                    f"{provider_name} 视频轮询",
                    attempt=attempt,
                    status=status,
                    interval=poll.interval,
                )
                await asyncio.sleep(poll.interval)
