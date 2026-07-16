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
    _to_text,
    PromptConfig,
    assign_sousuo_index,
    build_prompts,
    build_sousuo_prompts,
    find_category_prompt,
    parse_prompt_sections,
    pick_random_scene_prompt,
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

    async def _download_reference_images(
        self, ref_image_data: list[dict]
    ) -> list[bytes]:
        """下载记录附件里全部参考图，返回 list[bytes]；无有效 url 时返回 []。"""
        urls = [a.get("url") for a in ref_image_data if isinstance(a, dict)]
        urls = [u for u in urls if u]
        return await asyncio.gather(*[self.dingtalk.download_file(u) for u in urls])

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

            except asyncio.CancelledError:
                if table_config is not None:
                    await self._update_failure(table_config, record_id, "服务关闭导致任务中断")
                raise
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
        ref_image_bytes = await self._download_reference_images(ref_image_data)
        if not ref_image_bytes:
            await self._update_failure(table_config, record_id, "素材图 url 为空")
            return
        logger.info("步骤4: 素材图下载成功", record_id=record_id, count=len(ref_image_bytes), elapsed=self._elapsed(step_start))
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

        分流：
        - table_config.prompt_section_mode=True → 走 _process_batch_sousuo
          （三段式：每段 6 候选里抽 3 张，按 output_order 输出连续编号；
           zhuozhi-sousuo / ahmi-sousuo 当前都只配 "场景图"，输出 1-3 共 3 张）
        - 否则 → 走原 8 步逻辑（ahmi-batch-action 等）
        """
        # 0. 分流：搜推素材三段式（仅 zhuozhi-sousuo 启用）
        if table_config.prompt_section_mode:
            await self._process_batch_sousuo(record_id, table_config)
            return

        # 0a. 分流：基础素材模块（仅 zhuozhi-baseMaterial 启用）
        if table_config.base_material_mode:
            await self._process_batch_base_material(record_id, table_config)
            return

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
        ref_image_bytes = await self._download_reference_images(ref_image_data)
        if not ref_image_bytes:
            await self._update_failure(table_config, record_id, "素材图 url 为空")
            return

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

    # ========================================================================
    # 基础素材模块：单表三图按需生图（zhuozhi-baseMaterial 启用）
    # ========================================================================

    async def _process_batch_base_material(
        self, record_id: str, table_config: TableConfig
    ) -> None:
        """基础素材按需生图：白底图/场景图，两图独立判断已有则跳过。

        前置条件：table_config.base_material_mode=True 且启动时校验通过。

        两部判断逻辑：
        (1) 白底图已有 → 跳过；否则按类目匹配提示词生成 → 上传 {goodsId}_白底图.png → 回写
        (2) 场景图已有 → 跳过；否则从1-10中随机选提示词 → 上传 {goodsId}_场景图.png → 回写

        两图并发控制：通过 Semaphore（复用父类的 max_concurrency）限制，
        两张图依次执行，不抢占并发槽位。
        """
        step_start = time.monotonic()

        # 1. 取基础素材表格记录
        step = "获取记录数据"
        record = await self.dingtalk.get_record(table_config, record_id)
        fields = record.get("fields", {})
        goods_id = (
            _to_text(fields.get(table_config.goods_id_field))
            if table_config.goods_id_field else record_id
        )
        category = (
            _to_text(fields.get(table_config.category_field))
            if table_config.category_field else ""
        )
        model_raw = fields.get(table_config.model_field)
        logger.info(
            "基础素材流程开始",
            record_id=record_id, goods_id=goods_id,
            category=category, model=model_raw,
        )

        # 2. 检查两图已有状态
        wb_field = table_config.white_bg_image_field
        sc_field = table_config.scene_image_field
        white_bg_exists = bool(fields.get(wb_field)) if wb_field else False
        scene_exists = bool(fields.get(sc_field)) if sc_field else False
        logger.info(
            "基础素材已有图片状态",
            record_id=record_id,
            white_bg=white_bg_exists,
            scene=scene_exists,
        )

        # 两张图都已存在 → 直接返回
        if white_bg_exists and scene_exists:
            logger.info("两图均已存在，无生成任务", record_id=record_id)
            await self.dingtalk.update_record(
                table_config,
                record_id,
                {
                    table_config.result_status_field: "无生成任务",
                    table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                },
            )
            return

        # 3. 下载素材图（只要还有需要生成的图）
        step = "下载素材图"
        ref_image_data = fields.get(table_config.reference_image_field)
        if not isinstance(ref_image_data, list) or not ref_image_data:
            await self._update_failure(table_config, record_id, "素材图不能为空")
            return
        ref_image_bytes = await self._download_reference_images(ref_image_data)
        if not ref_image_bytes:
            await self._update_failure(table_config, record_id, "素材图 url 为空")
            return

        # 4. 查提示词表（按 task_name 过滤）
        step = "查提示词表"
        task_name = table_config.task_name
        prompt_records = await self.dingtalk.list_records(
            base_id=table_config.base_id,
            sheet_id=table_config.prompt_table_sheet_id,
            field="任务名称",
            value=task_name,
        )
        if not prompt_records:
            await self._update_failure(
                table_config, record_id,
                f"提示词表未找到 任务名称={task_name}",
            )
            return
        prompt_fields = (
            prompt_records[0].fields
            if hasattr(prompt_records[0], "fields")
            else prompt_records[0].get("fields", {})
        )
        prompt_text = _to_text(
            prompt_fields.get(table_config.prompt_table.perturbations_field)
        )
        if not prompt_text:
            await self._update_failure(
                table_config, record_id, "提示词表的扰动列表为空",
            )
            return

        # 5. 拆段：按"一、""二、" 拆分为 {白底图: [...], 场景图: [...]}
        step = "拆解三段式提示词"
        sections = parse_prompt_sections(prompt_text, table_config.section_titles or {})
        logger.info(
            "基础素材三段式解析",
            record_id=record_id,
            sections_found={k: len(v) for k, v in sections.items()},
        )

        # 6. 解析模型
        step = "解析模型"
        model = self._resolve_model(fields, table_config, self.settings.ai.default_model)

        # 7. 按需逐图生成
        step = "逐图生成"
        attachments: dict[str, dict] = {}  # {字段名: 附件信息}
        success_list: list[str] = []
        failure_list: list[str] = []

        # --- 7a. 白底图 ---
        if not white_bg_exists:
            await self._generate_base_material_image(
                record_id=record_id,
                table_config=table_config,
                section_name="白底图",
                category=category,
                sections=sections,
                model=model,
                ref_image_bytes=ref_image_bytes,
                goods_id=goods_id,
                aspect_ratio="1:1",
                attachments=attachments,
                success_list=success_list,
                failure_list=failure_list,
                target_field=table_config.white_bg_image_field or "",
                file_suffix="白底图",
            )

        # --- 7b. 场景图 ---
        if not scene_exists:
            scene_items = sections.get("场景图", [])
            if not scene_items:
                failure_list.append("场景图: 提示词表中无场景图段落")
            else:
                scene_result = pick_random_scene_prompt(scene_items)
                if scene_result is None:
                    failure_list.append("场景图: 提示词表场景图段落为空")
                else:
                    scene_prompt, _ = scene_result
                    logger.info(
                        "生成场景图",
                        record_id=record_id, goods_id=goods_id, prompt=scene_prompt[:60],
                    )
                    try:
                        scene_bytes = await self.generator.generate(
                            model=model,
                            prompt=scene_prompt,
                            reference_image=ref_image_bytes,
                            table_config=table_config,
                            aspect_ratio="1:1",
                        )
                        scene_attach = await self.dingtalk.upload_attachment(
                            table_config,
                            scene_bytes,
                            f"{goods_id}_场景图.png",
                        )
                        attachments[table_config.scene_image_field or ""] = scene_attach
                        success_list.append("场景图")
                        logger.info("场景图生成上传成功", record_id=record_id, goods_id=goods_id)
                    except Exception as e:
                        failure_list.append(f"场景图: {e}")
                        logger.error("场景图生成失败", record_id=record_id, error=str(e))

        # 8. 回写结果
        if success_list and not failure_list:
            status_text = f"成功: {', '.join(success_list)}"
        elif success_list and failure_list:
            status_text = f"成功: {', '.join(success_list)}; 失败: {'; '.join(failure_list)}"
        elif not success_list and failure_list:
            status_text = f"失败: {'; '.join(failure_list)}"
        else:
            # 理论上不会走到这里（前面已有无生成任务的 return）
            status_text = "无生成任务"

        update_fields: dict[str, object] = {
            table_config.result_status_field: status_text,
            table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        # 附加成功生成的图片
        for field_key, attach_info in attachments.items():
            if field_key:
                update_fields[field_key] = [attach_info]

        if attachments or status_text == "无生成任务":
            await self.dingtalk.update_record(
                table_config,
                record_id,
                update_fields,
            )
        else:
            await self._update_failure(table_config, record_id, status_text)
            return

        logger.info(
            "基础素材流程完成",
            record_id=record_id, goods_id=goods_id,
            result=status_text,
            elapsed_sec=round(time.monotonic() - step_start, 2),
        )

    async def _generate_base_material_image(
        self,
        record_id: str,
        table_config: TableConfig,
        section_name: str,
        category: str,
        sections: dict[str, list[str]],
        model: str,
        ref_image_bytes: bytes,
        goods_id: str,
        aspect_ratio: str,
        attachments: dict[str, dict],
        success_list: list[str],
        failure_list: list[str],
        target_field: str,
        file_suffix: str,
    ) -> None:
        """基础素材单项生成：按类目匹配提示词 → 生图 → 上传。

        作为 _process_batch_base_material 的子步骤，白底图使用此方法，
        场景图走独立的随机逻辑。
        """
        items = sections.get(section_name, [])
        if not items:
            failure_list.append(f"{section_name}: 提示词表中无{section_name}段落")
            return

        if not category:
            failure_list.append(f"{section_name}: 类目字段为空，无法匹配合适的提示词")
            return

        matched_prompt = find_category_prompt(items, category)
        if matched_prompt is None:
            failure_list.append(f"{section_name}: 未找到类目[{category}]的提示词")
            return

        logger.info(
            "生成{}", section_name,
            record_id=record_id, goods_id=goods_id,
            category=category,
            prompt=matched_prompt[:60],
        )
        try:
            img_bytes = await self.generator.generate(
                model=model,
                prompt=matched_prompt,
                reference_image=ref_image_bytes,
                table_config=table_config,
                aspect_ratio=aspect_ratio,
            )
            attach_info = await self.dingtalk.upload_attachment(
                table_config,
                img_bytes,
                f"{goods_id}_{file_suffix}.png",
            )
            if target_field:
                attachments[target_field] = attach_info
            success_list.append(section_name)
            logger.info(
                "{}生成上传成功", section_name,
                record_id=record_id, goods_id=goods_id,
            )
        except Exception as e:
            failure_list.append(f"{section_name}: {e}")
            logger.error(
                "{}生成失败", section_name,
                record_id=record_id, error=str(e),
            )

    # ========================================================================
    # 搜推素材三段式：长文本按段标题拆段，每段 6 候选里抽 3 张，
    # 按 output_order 连续编号（zhuozhi-sousuo / ahmi-sousuo 当前都只配 "场景图" → 1-3），
    # 文件名 {record_id}_{goods_id}_{shop_code}_{idx}.png
    # ========================================================================

    async def _process_batch_sousuo(
        self, record_id: str, table_config: TableConfig
    ) -> None:
        """搜推素材三段式批量生图（zhuozhi-sousuo / ahmi-sousuo 启用）。"""
        step_start = time.monotonic()

        # 1. 取生图表记录
        step = "获取记录数据"
        record = await self.dingtalk.get_record(table_config, record_id)
        fields = record.get("fields", {})

        # 2. 提取商品ID 和 店铺编码（用于自定义文件名）
        step = "提取商品ID和店铺编码"
        goods_id = _to_text(fields.get(table_config.goods_id_field)) if table_config.goods_id_field else ""
        shop_raw = _to_text(fields.get(table_config.shop_code_field)) if table_config.shop_code_field else ""
        shop_code = shop_raw.rsplit(table_config.shop_code_separator, 1)[-1].strip() if shop_raw else ""
        logger.info(
            "搜推素材命名字段",
            record_id=record_id,
            goods_id=goods_id,
            shop_raw=shop_raw,
            shop_code=shop_code,
        )

        # 3. 下载素材图（与原 batch 逻辑相同）
        step = "下载素材图"
        ref_image_data = fields.get(table_config.reference_image_field)
        if not isinstance(ref_image_data, list) or not ref_image_data:
            await self._update_failure(table_config, record_id, "素材图不能为空")
            return
        ref_image_bytes = await self._download_reference_images(ref_image_data)
        if not ref_image_bytes:
            await self._update_failure(table_config, record_id, "素材图 url 为空")
            return

        # 4. 按 task_name 查提示词表（与原 batch 逻辑相同）
        step = "查提示词表"
        task_name = table_config.task_name
        prompt_records = await self.dingtalk.list_records(
            base_id=table_config.base_id,
            sheet_id=table_config.prompt_table_sheet_id,
            field="任务名称",
            value=task_name,
        )
        if not prompt_records:
            await self._update_failure(
                table_config, record_id,
                f"提示词表未找到 任务名称={task_name}",
            )
            return
        prompt_fields = (
            prompt_records[0].fields
            if hasattr(prompt_records[0], "fields")
            else prompt_records[0].get("fields", {})
        )
        # 搜推素材：三段式段落源是「扰动列表」字段（不是「提示词」字段）
        # 「提示词」字段是通用基础描述，作为 base_prompt 拼接
        prompt_text = _to_text(
            prompt_fields.get(table_config.prompt_table.perturbations_field)
        )
        base_prompt = _to_text(
            prompt_fields.get(table_config.prompt_table.prompt_field)
        )
        if not prompt_text:
            await self._update_failure(
                table_config, record_id, "提示词表的扰动列表为空",
            )
            return
        aspect_ratio = _to_text(prompt_fields.get(table_config.prompt_table.aspect_ratio_field))
        resolution = _to_text(prompt_fields.get(table_config.prompt_table.resolution_field))

        # 4.5. 读生成数量（提示词表「生成数量」字段，缺省回落 table_config.count_per_section）
        # 钉钉单元格里「生成数量」通常是字符串（"6"）或数字 6，统一转 int
        count_raw = prompt_fields.get(table_config.prompt_table.count_field)
        try:
            count_per_section = int(count_raw) if count_raw not in (None, "") else 0
        except (TypeError, ValueError):
            count_per_section = 0
        if count_per_section <= 0:
            count_per_section = table_config.count_per_section
            logger.warning(
                "提示词表「生成数量」无效（{!r}），回落 table_config.count_per_section={}",
                count_raw, count_per_section,
            )

        # 5. 解析模型（同原逻辑）
        step = "解析模型"
        model = self._resolve_model(fields, table_config, self.settings.ai.default_model)

        # 6. 拆段 + 重组（搜推素材特有）
        step = "拆解三段式提示词"
        sections = parse_prompt_sections(prompt_text, table_config.section_titles or {})
        logger.info(
            "三段式提示词解析",
            record_id=record_id,
            sections_found={k: len(v) for k, v in sections.items()},
            output_order=table_config.output_order,
            count_per_section=count_per_section,
        )
        # 缺任何一段都直接失败，避免生成不全
        missing = [t for t in (table_config.output_order or []) if t not in sections]
        if missing:
            await self._update_failure(
                table_config, record_id,
                f"提示词表缺少段: {', '.join(missing)}",
            )
            return
        # 用 record_id 作为 seed，保证同 record 多次跑结果一致
        ordered = build_sousuo_prompts(
            base_prompt=base_prompt,
            sections=sections,
            output_order=table_config.output_order or [],
            count_per_section=count_per_section,
            seed=record_id,
        )
        indexed = assign_sousuo_index(ordered, count_per_section)
        prompts = [p for p, _, _ in indexed]
        logger.info(
            "本次搜推素材批量生图配置",
            record_id=record_id,
            task_name=task_name,
            total=len(prompts),
            indexed=indexed,
        )

        # 7. 批量生图
        step = "批量生图"
        results = await self.generator.generate_batch(
            model=model,
            prompts=prompts,
            reference_image=ref_image_bytes,
            table_config=table_config,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        success_count = sum(1 for r in results if r is not None)
        if success_count == 0:
            await self._update_failure(
                table_config, record_id,
                f"全部 {len(prompts)} 张生图失败",
            )
            return

        # 8. 串行上传 + 自定义命名
        step = "上传结果"
        attachments: list[dict] = []
        upload_errors: list[str] = []
        for (prompt, tname, idx), img_bytes in zip(indexed, results):
            if img_bytes is None:
                continue
            filename = f"{record_id}_{goods_id}_{shop_code}_{idx}.png"
            try:
                att = await self.dingtalk.upload_attachment(
                    table_config,
                    img_bytes,
                    filename,
                )
                attachments.append(att)
            except Exception as e:
                upload_errors.append(f"{tname}#{idx}上传失败: {e}")
                logger.error(
                    "单图上传失败", record_id=record_id,
                    segment=tname, index=idx, filename=filename, error=str(e),
                )

        # 9. 回写
        step = "回写"
        status_text = f"成功{len(attachments)}/{len(results)}"
        if upload_errors:
            status_text += f"; {'; '.join(upload_errors)}"
        if attachments:
            # 优先写「附件 + 时间」（这两列一定能写），失败再降级只写附件
            base_fields = {
                table_config.result_image_field: attachments,
                table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            try:
                await self.dingtalk.update_record(
                    table_config,
                    record_id,
                    {**base_fields, table_config.result_status_field: status_text},
                )
            except Exception as status_err:
                # 状态字段可能是 singleSelect 枚举（写自由文本会被拒 400），不影响主结果回写
                logger.warning(
                    "写状态字段失败，降级只写附件 record_id={} err={}",
                    record_id, status_err,
                )
                try:
                    await self.dingtalk.update_record(table_config, record_id, base_fields)
                except Exception:
                    tb = traceback.format_exc()
                    logger.error(
                        "附件回写失败 record_id={} table_key={}\n{}",
                        record_id, table_config.key, tb,
                    )
        else:
            await self._update_failure(
                table_config, record_id,
                f"全部 {len(results)} 张上传失败: {'; '.join(upload_errors)}",
            )
            return
        logger.info(
            "搜推素材批量生图完成", record_id=record_id,
            task_name=task_name,
            success=len(attachments), total=len(results),
        )
        logger.info(
            "搜推素材批量生图耗时",
            record_id=record_id,
            elapsed_sec=round(time.monotonic() - step_start, 2),
        )
