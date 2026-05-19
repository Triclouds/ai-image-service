"""生图编排服务。

完整流程：获取记录 → 校验字段 → 下载素材图 → 调用AI生图 → 上传结果 → 回写表格。
"""

import asyncio
from datetime import datetime, timezone

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
        except Exception as e:
            logger.exception(f"回写失败状态失败 record_id={record_id}")

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
        table_config = self.settings.get_table(table_key)

        async with self._semaphore:
            try:
                # 1. 获取记录数据
                record = await self.dingtalk.get_record(table_config, record_id)

                # 2. 校验提示词
                prompt = record.get("fields", {}).get(table_config.prompt_field)
                if not prompt:
                    await self._update_failure(table_config, record_id, "提示词不能为空")
                    return

                # 3. 校验素材图
                ref_image_data = record.get("fields", {}).get(table_config.reference_image_field)
                if not ref_image_data:
                    await self._update_failure(table_config, record_id, "素材图不能为空")
                    return

                # 4. 下载素材图
                ref_image_bytes = await self.dingtalk.download_file(ref_image_data[0]["url"])

                # 5. 获取模型并调用AI生图
                model = record.get("fields", {}).get(
                    table_config.model_field, self.settings.ai.default_model
                )
                result_bytes = await self.generator.generate(
                    model=model,
                    prompt=prompt,
                    reference_image=ref_image_bytes,
                )

                # 6. 上传结果图片
                attachment_info = await self.dingtalk.upload_attachment(
                    table_config,
                    result_bytes,
                    f"generated_{record_id}.png",
                )

                # 7. 回写成功状态
                await self.dingtalk.update_record(
                    table_config,
                    record_id,
                    {
                        table_config.result_image_field: [attachment_info],
                        table_config.result_status_field: "成功",
                        table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                    },
                )

            except Exception as e:
                logger.exception(f"生图失败 record_id={record_id}")
                await self._update_failure(table_config, record_id, str(e))