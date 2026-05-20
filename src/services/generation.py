"""生图编排服务。

完整流程：获取记录 → 校验字段 → 下载素材图 → 调用AI生图 → 上传结果 → 回写表格。
"""

import asyncio
import time
import traceback
from datetime import datetime

from loguru import logger

from config import Settings, TableConfig
from dingtalk.client import DingTalkClient
from generator import AIGenerator


class GenerationService:
    """生图编排服务。"""

    def __init__(
        self,
        dingtalk: DingTalkClient,
        generator: AIGenerator,
        settings: Settings,
    ):
        self.dingtalk = dingtalk
        self.generator = generator
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.server.max_concurrency)

    @staticmethod
    def _elapsed(start: float) -> str:
        """返回格式化耗时字符串，如 '1.2s' 或 '68s'。"""
        ms = (time.monotonic() - start) * 1000
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{ms / 1000:.1f}s"

    async def _update_failure(
        self,
        table_config: TableConfig,
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
            logger.info(f"已回写失败状态到表格 record_id={record_id} error={error_message}")
        except Exception:
            tb = traceback.format_exc()
            logger.error(f"回写失败状态失败 record_id={record_id} table_key={table_config.key} original_error={error_message}\n{tb}")

    async def process(self, record_id: str, table_key: str | None = None) -> None:
        """完整生图流程。

        1. 获取表格配置（根据 table_key）
        2. 获取记录数据
        3. 校验：提示词必填、素材图必填
        4. 下载素材图（图生图输入）
        5. 调用AI生图
        6. 上传结果图片到钉钉云空间
        7. 回写结果到钉钉表格

        本方法不做重试。各网络调用方法已通过 @retry_on_network_error 独立处理重试。
        并发控制：通过 Semaphore 限制最大并发生图数，超出时排队等待（无超时）。
        错误处理：业务错误统一回写表格"失败: {str(e)}"。
        """
        async with self._semaphore:
            table_config: TableConfig | None = None
            step = "初始化"
            step_start = time.monotonic()
            try:
                step = "开始"
                logger.info("生图流程开始", record_id=record_id, table_key=table_key)

                step = "获取表格配置"
                table_config = self.settings.get_table(table_key)
                logger.info(
                    "步骤1: 获取表格配置成功", record_id=record_id, table_key=table_config.key,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 1. 获取记录数据
                step = "获取记录数据"
                record = await self.dingtalk.get_record(table_config, record_id)
                fields = record.get("fields", {})
                prompt_raw = fields.get(table_config.prompt_field, "")
                model_raw = fields.get(table_config.model_field)
                ref_images = fields.get(table_config.reference_image_field)
                ref_filenames = ", ".join(
                    [img.get("filename", "未知") for img in ref_images]
                ) if ref_images else "无"
                logger.info(
                    "步骤2: 获取记录数据成功",
                    record_id=record_id,
                    prompt=prompt_raw[:80] if prompt_raw else "",
                    model=model_raw,
                    ref_images=ref_filenames,
                    elapsed=self._elapsed(step_start),
                )
                step_start = time.monotonic()

                # 2. 校验提示词
                step = "校验提示词"
                prompt = fields.get(table_config.prompt_field)
                if not prompt:
                    logger.warning("提示词为空", record_id=record_id)
                    await self._update_failure(table_config, record_id, "提示词不能为空")
                    return

                # 3. 校验素材图
                step = "校验素材图"
                ref_image_data = fields.get(table_config.reference_image_field)
                if not ref_image_data:
                    logger.warning("素材图为空", record_id=record_id)
                    await self._update_failure(table_config, record_id, "素材图不能为空")
                    return

                logger.info("步骤3: 校验通过", record_id=record_id, prompt_len=len(prompt), elapsed=self._elapsed(step_start))
                step_start = time.monotonic()

                # 4. 下载素材图
                step = "下载素材图"
                ref_image_bytes = await self.dingtalk.download_file(ref_image_data[0]["url"])
                logger.info("步骤4: 素材图下载成功", record_id=record_id, size=len(ref_image_bytes), elapsed=self._elapsed(step_start))
                step_start = time.monotonic()

                # 5. 获取模型并调用AI生图
                step = "调用AI生图"
                model_raw = fields.get(table_config.model_field)
                if isinstance(model_raw, str):
                    model = model_raw
                elif isinstance(model_raw, dict):
                    model = model_raw.get("name", self.settings.ai.default_model)
                else:
                    model = self.settings.ai.default_model
                logger.info("步骤5: 调用 AI 生图", record_id=record_id, model=model)
                result_bytes = await self.generator.generate(
                    model=model,
                    prompt=prompt,
                    reference_image=ref_image_bytes,
                    table_config=table_config,
                )
                logger.info("步骤5: AI 生图完成", record_id=record_id, size=len(result_bytes), elapsed=self._elapsed(step_start))
                step_start = time.monotonic()

                # 6. 上传结果图片
                step = "上传结果图片"
                logger.info("步骤6: 上传结果图片到钉钉", record_id=record_id)
                attachment_info = await self.dingtalk.upload_attachment(
                    table_config,
                    result_bytes,
                    f"generated_{record_id}.png",
                )
                logger.info("步骤6: 上传成功", record_id=record_id, elapsed=self._elapsed(step_start))
                step_start = time.monotonic()

                # 7. 回写成功状态
                step = "回写成功状态"
                await self.dingtalk.update_record(
                    table_config,
                    record_id,
                    {
                        table_config.result_image_field: [attachment_info],
                        table_config.result_status_field: "成功",
                        table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                    },
                )
                logger.info("生图流程完成", record_id=record_id, elapsed=self._elapsed(step_start))

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"生图流程异常 record_id={record_id} table_key={table_config.key if table_config else None} step={step}\n{tb}"
                )
                if table_config is not None:
                    await self._update_failure(table_config, record_id, f"[{step}] {e}")
