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
from models.prompt_config import (
    _FALLBACK_SUFFIX_START,
    _extract_pert_number,
    PromptConfig,
    build_prompts,
)


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

    @staticmethod
    def _resolve_model(fields: dict, table_config: TableConfig, default: str) -> str:
        """从 fields[model_field] 解析出模型名称，缺失则回退 default。"""
        raw = fields.get(table_config.model_field)
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return raw.get("name", default)
        return default

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
        """根据 batch_mode 分流到单图或批量生图流程。"""
        async with self._semaphore:
            table_config: TableConfig | None = None
            step = "初始化"
            try:
                step = "获取表格配置"
                table_config = self.settings.get_table(table_key)
                logger.info(
                    "生图流程开始",
                    record_id=record_id,
                    table_key=table_config.key,
                    batch_mode=table_config.batch_mode,
                )

                if table_config.batch_mode:
                    await self._process_batch(record_id, table_config)
                else:
                    await self._process_single(record_id, table_config)

            except Exception as e:
                tb = traceback.format_exc()
                logger.error(f"生图流程异常 record_id={record_id} step={step}\n{tb}")
                if table_config is not None:
                    await self._update_failure(table_config, record_id, f"[{step}] {e}")

    async def _process_single(
        self, record_id: str, table_config: TableConfig
    ) -> None:
        """单图生图流程（既有逻辑）。"""
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
        model = self._resolve_model(fields, table_config, self.settings.ai.default_model)
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

    async def _process_batch(
        self, record_id: str, table_config: TableConfig
    ) -> None:
        """批量生图流程：双表查询 + 多图生成 + 串行上传 + 回写。

        前置条件：table_config.batch_mode=true 且启动时校验通过。
        """
        step_start = time.monotonic()

        # 1. 取生图表记录
        record = await self.dingtalk.get_record(table_config, record_id)
        fields = record.get("fields", {})

        # 2. task_name 从配置读（不是从 fields 读）
        step = "读取 task_name"
        task_name = table_config.task_name
        logger.info("批量生图配置", record_id=record_id, task_name=task_name)

        # 3. 校验 + 下载素材图
        step = "下载素材图"
        ref_image_data = fields.get(table_config.reference_image_field)
        if not isinstance(ref_image_data, list) or not ref_image_data:
            await self._update_failure(table_config, record_id, "素材图不能为空")
            return
        ref_image_url = ref_image_data[0].get("url")
        if not ref_image_url:
            await self._update_failure(table_config, record_id, "素材图 url 为空")
            return
        ref_image_bytes = await self.dingtalk.download_file(ref_image_url)

        # 4. 按 task_name 查提示词表
        step = "查提示词表"
        prompt_records = await self.dingtalk.list_records(
            base_id=table_config.base_id,
            sheet_id=table_config.prompt_table_sheet_id,
            field="任务名称",                      # 硬编码字面量
            value=task_name,
        )
        if not prompt_records:
            await self._update_failure(
                table_config, record_id,
                f"提示词表未找到 任务名称={task_name}",
            )
            return
        prompt_cfg = PromptConfig.from_prompt_record(
            prompt_records[0].fields or {}, table_config.prompt_table
        )
        if not prompt_cfg.prompt:
            await self._update_failure(
                table_config, record_id, "提示词表的提示词为空",
            )
            return

        # 5. 解析模型（复用公共方法）
        step = "解析模型"
        model = self._resolve_model(fields, table_config, self.settings.ai.default_model)

        # 6. 批量生图
        step = "批量生图"
        prompts = build_prompts(prompt_cfg)
        logger.info(
            "本次批量生图配置",
            record_id=record_id,
            task_name=task_name,
            count=prompt_cfg.effective_count,
            perturbations=len(prompt_cfg.perturbations),
            prompts=len(prompts),
        )
        results = await self.generator.generate_batch(
            model=model, prompts=prompts,
            reference_image=ref_image_bytes,
            table_config=table_config,
            aspect_ratio=prompt_cfg.aspect_ratio,
            resolution=prompt_cfg.resolution,
        )
        success_count = sum(1 for r in results if r is not None)
        if success_count == 0:
            await self._update_failure(
                table_config, record_id,
                f"全部 {len(prompts)} 张生图失败",
            )
            return

        # 7. 串行上传
        step = "上传结果"
        attachments: list[dict] = []
        upload_errors: list[str] = []
        fallback_counter = _FALLBACK_SUFFIX_START
        for i, img_bytes in enumerate(results):
            if img_bytes is None:
                continue
            # 文件名后缀：扰动文本带编号（"1. xxx"）用其编号；无编号从 101 递增
            if prompt_cfg.perturbations:
                pert = prompt_cfg.perturbations[i % len(prompt_cfg.perturbations)]
                pert_num = _extract_pert_number(pert)
            else:
                pert_num = None
            if pert_num is not None:
                suffix = pert_num
            else:
                suffix = fallback_counter
                fallback_counter += 1
            try:
                att = await self.dingtalk.upload_attachment(
                    table_config,
                    img_bytes,
                    f"generated_{record_id}_{suffix}.png",
                )
                attachments.append(att)
            except Exception as e:
                upload_errors.append(f"第{i+1}张上传失败: {e}")
                logger.error(
                    "单图上传失败", record_id=record_id, index=i+1, error=str(e),
                )

        # 8. 回写
        step = "回写"
        status_text = f"成功{len(attachments)}/{len(results)}"
        if upload_errors:
            status_text += f"; {'; '.join(upload_errors)}"
        if attachments:
            await self.dingtalk.update_record(
                table_config,
                record_id,
                {
                    table_config.result_image_field: attachments,
                    table_config.result_status_field: status_text,
                    table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                },
            )
        else:
            # 生成成功但全部上传失败 → 走 _update_failure 写"失败: ..." 与单图契约对齐
            await self._update_failure(
                table_config, record_id,
                f"全部 {len(results)} 张上传失败: {'; '.join(upload_errors)}",
            )
            return
        logger.info(
            "批量生图完成", record_id=record_id,
            task_name=task_name,
            success=len(attachments), total=len(results),
        )
        logger.info(
            "批量生图耗时",
            record_id=record_id,
            elapsed_sec=round(time.monotonic() - step_start, 2),
        )
