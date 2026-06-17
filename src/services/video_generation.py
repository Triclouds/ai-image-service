"""视频生成编排服务。

完整 9 步流程（与 GenerationService 7 步骨架对称，第 5/6/7 步在
video_generator.generate() 内部完成：编码 base64 → 提交任务 → 轮询 → 下载 mp4）。
"""

import asyncio
import time
import traceback
from datetime import datetime

from loguru import logger

from config import Settings, VideoTableConfig
from dingtalk.client import DingTalkClient
from generator.video_engine import VideoGenerator


class VideoGenerationService:
    """视频生成编排服务。

    并发控制：通过独立 Semaphore (settings.server.video_max_concurrency)
    限制最大并发视频任务数，与图片 Service 的 max_concurrency 完全独立。
    """

    def __init__(
        self,
        dingtalk: DingTalkClient,
        video_generator: VideoGenerator,
        settings: Settings,
    ):
        self.dingtalk = dingtalk
        self.video_generator = video_generator
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.server.video_max_concurrency)

    @staticmethod
    def _elapsed(start: float) -> str:
        """返回格式化耗时字符串，如 '1.2s' 或 '68s'。"""
        ms = (time.monotonic() - start) * 1000
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{ms / 1000:.1f}s"

    async def _update_failure(
        self,
        table_config: VideoTableConfig,
        record_id: str,
        error_message: str,
    ) -> None:
        """回写失败状态到钉钉表格。"""
        try:
            await self.dingtalk.update_record(
                table_config,
                record_id,
                {
                    table_config.result_status_field: f"失败: {error_message}",
                    table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                },
            )
            logger.info(f"已回写失败状态到视频表 record_id={record_id} error={error_message}")
        except Exception:
            tb = traceback.format_exc()
            logger.error(
                f"回写失败状态失败 record_id={record_id} table_key={table_config.key} "
                f"original_error={error_message}\n{tb}"
            )

    async def process(self, record_id: str, table_key: str) -> None:
        """完整视频生成流程（9 步）。

        1. 获取表格配置
        2. 获取记录数据
        3. 校验：提示词 / 视频模型 / 首帧图 必填
        4. 下载首帧图
        5-7. 调用 VideoGenerator.generate() 内部完成 base64 编码、提交、轮询、下载 mp4
        8. 上传 mp4 到钉钉云空间（media_type=video/mp4）
        9. 回写表格

        本方法不做重试。各网络调用方法已通过 _retry_on_network_error 独立处理重试。
        并发控制：通过 Semaphore 限制最大并发视频任务数，超出时排队等待（无超时）。
        错误处理：业务错误统一回写表格"失败: {str(e)}"。
        """
        async with self._semaphore:
            table_config: VideoTableConfig | None = None
            step = "初始化"
            step_start = time.monotonic()
            try:
                step = "开始"
                logger.info("视频流程开始", record_id=record_id, table_key=table_key)

                # 1. 获取表格配置
                step = "获取表格配置"
                table_config = self.settings.get_video_table(table_key)
                logger.info(
                    "步骤1: 获取视频表格配置成功",
                    record_id=record_id,
                    table_key=table_config.key,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 2. 获取记录数据
                step = "获取记录数据"
                record = await self.dingtalk.get_record(table_config, record_id)
                fields = record.get("fields", {})
                prompt_raw = fields.get(table_config.prompt_field, "")
                model_raw = fields.get(table_config.video_model_field)
                ref_images = fields.get(table_config.reference_image_field)
                ref_filenames = ", ".join(
                    [img.get("filename", "未知") for img in ref_images]
                ) if ref_images else "无"
                logger.info(
                    "步骤2: 获取视频记录数据成功",
                    record_id=record_id,
                    prompt=prompt_raw[:80] if prompt_raw else "",
                    video_model=model_raw,
                    ref_images=ref_filenames,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 3. 校验必填字段（提示词 / 视频模型 / 首帧图）
                step = "校验必填字段"
                prompt = fields.get(table_config.prompt_field)
                if not prompt:
                    logger.warning("提示词为空", record_id=record_id)
                    await self._update_failure(table_config, record_id, "提示词不能为空")
                    return
                video_model = fields.get(table_config.video_model_field)
                if not video_model or not isinstance(video_model, str):
                    logger.warning("视频模型为空", record_id=record_id)
                    await self._update_failure(table_config, record_id, "视频模型不能为空")
                    return
                ref_image_data = fields.get(table_config.reference_image_field)
                if not ref_image_data:
                    logger.warning("首帧图为空", record_id=record_id)
                    await self._update_failure(table_config, record_id, "首帧图不能为空")
                    return

                logger.info(
                    "步骤3: 校验通过",
                    record_id=record_id,
                    prompt_len=len(prompt),
                    video_model=video_model,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 4. 下载首帧图
                step = "下载首帧图"
                ref_image_bytes = await self.dingtalk.download_file(ref_image_data[0]["url"])
                logger.info(
                    "步骤4: 首帧图下载成功",
                    record_id=record_id,
                    size=len(ref_image_bytes),
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 5-7. 调用 VideoGenerator 内部完成 base64 编码、提交、轮询、下载 mp4
                step = "调用视频生成"
                logger.info(
                    "步骤5-7: 调用 VideoGenerator",
                    record_id=record_id,
                    video_model=video_model,
                )
                mp4_bytes = await self.video_generator.generate(
                    model=video_model,
                    prompt=prompt,
                    reference_image=ref_image_bytes,
                    table_config=table_config,
                )
                logger.info(
                    "步骤5-7: 视频生成完成",
                    record_id=record_id,
                    size=len(mp4_bytes),
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 8. 上传 mp4 到钉钉云空间
                step = "上传视频到钉钉"
                logger.info("步骤8: 上传视频到钉钉", record_id=record_id)
                attachment_info = await self.dingtalk.upload_attachment(
                    table_config,
                    mp4_bytes,
                    f"generated_{record_id}.mp4",
                    media_type="video/mp4",
                )
                logger.info(
                    "步骤8: 上传成功",
                    record_id=record_id,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 9. 回写成功状态
                step = "回写成功状态"
                await self.dingtalk.update_record(
                    table_config,
                    record_id,
                    {
                        table_config.result_video_field: [attachment_info],
                        table_config.result_status_field: "成功",
                        table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                    },
                )
                logger.info("视频流程完成", record_id=record_id, elapsed=self._elapsed(step_start))

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"视频流程异常 record_id={record_id} "
                    f"table_key={table_config.key if table_config else None} "
                    f"step={step}\n{tb}"
                )
                if table_config is not None:
                    await self._update_failure(table_config, record_id, f"[{step}] {e}")
