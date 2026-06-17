# 批量生图改造计划

> 目标：在 ai-image-service 中为现有 `[[dingtalk.tables]]` 配置段新增「批量生图」模式，保持现有 3 张单图表和 HTTP 接口完全不动。
>
> 参考实现：`F:\MyCode\PythonProject\aigenerated_images`（aigenerated_images）
>
> 当前项目：`F:\MyCode\PythonProject\ai-image-service`（ai-image-service）

---

## 1. 关键决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | 配置段 | **复用 `[[dingtalk.tables]]`**，不加新的平级段 |
| 2 | 模式区分 | `TableConfig.batch_mode: bool = False` |
| 3 | 一张 sheet 对应一个任务 | `task_name` 写在配置里（不是钉钉表格字段）。同一 sheet 内所有行共用同一个 `task_name` |
| 4 | 跨表查询 | 钉钉 SDK `list_records_with_options_async`，按 `任务名称` 列过滤 |
| 5 | HTTP 接口 | **完全不变**：`POST /api/v1/generate {record_id, table_key}` |
| 6 | 服务类 | **复用 `GenerationService`**：内部 `if batch_mode` 分流 |
| 7 | 现有 3 张表 | 完全不动 |

**关联方式**：每张 sheet 在配置里写死对应的 `task_name`（如"动作图-A"）。HTTP 触发时，后端从配置读出该 sheet 的 `task_name`，去提示词表里查 `任务名称 == task_name` 的那一行。

---

## 2. 数据流

```
HTTP POST /api/v1/generate {record_id, table_key}
  ↓
GenerationService.process(record_id, table_key)
  ↓
table_config = settings.get_table(table_key)
  ↓
if table_config.batch_mode:
    ↓
    _process_batch(record_id, table_config):
      [1] get_record(image_sheet, record_id)
          → 拿到素材图、模型等（fields 里没有任务名称）
      [2] task_name = table_config.task_name（从配置读）
      [3] 校验素材图非空、下载素材图
      [4] list_records(prompt_table_sheet, field="任务名称", value=task_name)
          → 找到提示词表里「任务名称」== task_name 的记录
      [5] PromptConfig.from_prompt_record(fields, prompt_table)
      [6] build_prompts(prompt_cfg) → N 个 prompt
      [7] generator.generate_batch(model, prompts, ref_img) → N 张图
      [8] 串行 upload_attachment × N
      [9] update_record(回写附件数组 + 状态)
else:
    ↓
    _process_single(record_id, table_config):  # 现有逻辑
```

---

## 3. 文件改动清单

| # | 文件 | 类型 | 改动 |
|---|------|------|------|
| 1 | `src/config.py` | 改 | `TableConfig` 加 `batch_mode` + `task_name` + 批量字段；新增 `PromptTableConfig`；加启动时校验 |
| 2 | `src/models/prompt_config.py` | 新增 | `PromptConfig` + `build_prompts` |
| 3 | `src/models/__init__.py` | 改 | 导出 `PromptConfig` |
| 4 | `src/dingtalk/client.py` | 改 | 新增 `list_records`；现有 `get_record` / `update_record` / `upload_attachment` 不动 |
| 5 | `src/generator/engine.py` | 改 | 新增 `generate_batch()`（签名含可选参数 `reference_image`、`table_config`） |
| 6 | `src/services/generation.py` | 改 | `process()` 加 `batch_mode` 分支；现有逻辑搬到 `_process_single()`；新增 `_process_batch()` |
| 7 | `tests/test_models/test_prompt_config.py` | 新增 | PromptConfig 单测 |
| 8 | `tests/test_dingtalk/test_client.py` | 改 | 新增 `list_records` 单测；保留旧单测 |
| 9 | `tests/test_generator/test_engine.py` | 新增 | `generate_batch` 单测 |
| 10 | `tests/test_services/test_generation.py` | 改 | 新增批量分支用例；保留单图用例 |
| 11 | `docs/PLAN-batch-image-generation.md` | 改 | 本文档 |
| 12 | `docs/ROADMAP.md` | 改 | 追加里程碑 |

**不动的文件**：`src/main.py`、`src/api/router.py`、`src/api/deps.py`、`src/models/request.py`、`src/models/response.py`、`src/generator/video_engine.py`、`src/services/video_generation.py`、`src/utils/*`。

---

## 4. 详细改动

### 4.1 `src/config.py`

#### 4.1.1 新增 `PromptTableConfig`

```python
class PromptTableConfig(BaseModel):
    """提示词表字段映射，对应 [dingtalk.tables.prompt_table]。"""

    prompt_field: str = "提示词"
    generate_type_field: str = "生成类型"
    count_field: str = "生成数量"
    aspect_ratio_field: str = "生成比例"
    resolution_field: str = "分辨率"
    perturbations_field: str = "扰动列表"
```

> 提示词表里的「任务名称」列硬编码为字面量 `"任务名称"`，不做配置项。

#### 4.1.2 `TableConfig` 扩展

```python
class TableConfig(BaseModel):
    key: str
    base_id: str
    sheet_id: str
    image_api_key_env: str

    # 模式开关
    batch_mode: bool = False

    # 批量模式必填字段
    task_name: str | None = None               # 该 sheet 对应的任务名称（从配置读）
    prompt_table_sheet_id: str | None = None   # 提示词表 sheet_id
    prompt_table: PromptTableConfig | None = None

    # 单图模式字段（batch_mode=false 时使用）
    prompt_field: str = "提示词"

    # 共享字段
    model_field: str = "生图模型"
    reference_image_field: str = "素材图"
    result_image_field: str = "生成图片"
    result_status_field: str = "生成结果"
    result_time_field: str = "生成时间"

    # 可选增强字段
    error_field: str | None = None
    style_code_field: str | None = None
    run_account_field: str | None = None
```

#### 4.1.3 加载逻辑适配

在 `_load_toml_config` 处理 `tables` 列表时，逐项构造时 pydantic 自动递归 `prompt_table` 嵌套 dict。如果需要手动转换，在循环里加：

```python
for t in dt_data["tables"]:
    if "prompt_table" in t and t["prompt_table"] is not None:
        t["prompt_table"] = PromptTableConfig(**t["prompt_table"])
```

#### 4.1.4 启动时校验

新增方法 `_validate_batch_mode_config`，在 `Settings.__init__` 末尾调用：

```python
def _validate_batch_mode_config(self) -> None:
    for table in self.dingtalk.tables:
        if not table.batch_mode:
            continue
        missing = []
        if not table.task_name:
            missing.append("task_name")
        if not table.prompt_table_sheet_id:
            missing.append("prompt_table_sheet_id")
        if table.prompt_table is None:
            missing.append("prompt_table")
        if missing:
            raise ConfigError(
                f"Table '{table.key}' has batch_mode=true but missing: {', '.join(missing)}"
            )
```

#### 4.1.5 config.toml 示例

```toml
# ===== 现有 3 张表（完全不动）=====
[[dingtalk.tables]]
key = "zhuozhi-base"
base_id = "ZgpG2NdyVX6bMqMaTGgr2nLG8MwvDqPk"
sheet_id = "hERWDMS"
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"
prompt_field = "提示词"
model_field = "生图模型"
reference_image_field = "素材图"
result_image_field = "生成图片"
result_status_field = "生成结果"
result_time_field = "生成时间"

# ===== 新批量表（动作图 sheet，对应任务"动作图-A"）=====
# 复用现有 ZHUOZHI_IMAGE_API_KEY：批量表与单图表共享同一中转站账号，
# 凭证上没区别，只是流程不同。无需新增 BATCH_IMAGE_API_KEY 这类 key。
[[dingtalk.tables]]
key = "zhuozhi-batch-action"
base_id = "<新建 base 的 id>"
sheet_id = "<动作图 sheet_id>"
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"

batch_mode = true
task_name = "动作图-A"                         # ← 该 sheet 对应的任务
prompt_table_sheet_id = "<提示词表 sheet_id>"

model_field = "生图模型"
reference_image_field = "模特标准图"
result_image_field = "AI模特动作图"
result_status_field = "动作图状态"
result_time_field = "动作图执行时间"
error_field = "运行情况"
style_code_field = "款式编码"
run_account_field = "运行账号"

[dingtalk.tables.prompt_table]
prompt_field = "提示词"
generate_type_field = "生成类型"
count_field = "生成数量"
aspect_ratio_field = "生成比例"
resolution_field = "分辨率"
perturbations_field = "扰动列表"

# ===== 新批量表（姿态图 sheet，对应任务"姿态图-A"，共享同一提示词表）=====
# 同一中转站账号 → 继续复用 ZHUOZHI_IMAGE_API_KEY。
[[dingtalk.tables]]
key = "zhuozhi-batch-pose"
base_id = "<同一 base>"
sheet_id = "<姿态图 sheet_id>"
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"

batch_mode = true
task_name = "姿态图-A"                         # ← 不同的任务
prompt_table_sheet_id = "<同一提示词表 sheet_id>"

model_field = "生图模型"
reference_image_field = "模特标准图"
result_image_field = "AI模特姿态图"
result_status_field = "姿态图状态"
result_time_field = "姿态图执行时间"
error_field = "运行情况"

[dingtalk.tables.prompt_table]
prompt_field = "提示词"
count_field = "生成数量"
perturbations_field = "扰动列表"
# 其他字段同理
```

---

### 4.2 `src/models/prompt_config.py`（新增）

```python
"""提示词配置数据模型 + 批量生图的 prompt 构建辅助。"""
from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return "".join(
                item.get("text", "") for item in value if isinstance(item, dict)
            )
        return str(value[0])
    return str(value)


def _split_perturbations(value: Any) -> list[str]:
    text = _to_text(value)
    return [p.strip() for p in text.split("\n") if p.strip()]


@dataclass
class PromptConfig:
    """从提示词表 fields 解析出的提示词配置。"""

    prompt: str
    count: int = 1
    perturbations: list[str] = field(default_factory=list)
    generate_type: str = ""
    aspect_ratio: str = ""
    resolution: str = ""

    @classmethod
    def from_prompt_record(
        cls, fields: dict[str, Any], prompt_table: "PromptTableConfig"
    ) -> "PromptConfig":
        return cls(
            prompt=_to_text(fields.get(prompt_table.prompt_field)),
            generate_type=_to_text(fields.get(prompt_table.generate_type_field)),
            count=_to_int(fields.get(prompt_table.count_field), 1),
            aspect_ratio=_to_text(fields.get(prompt_table.aspect_ratio_field)),
            resolution=_to_text(fields.get(prompt_table.resolution_field)),
            perturbations=_split_perturbations(
                fields.get(prompt_table.perturbations_field)
            ),
        )

    @property
    def effective_count(self) -> int:
        return max(1, self.count)


def build_prompts(prompt_cfg: PromptConfig) -> list[str]:
    """根据 PromptConfig 构建完整 prompt 列表。

    扰动按索引对齐；不足时循环复用；count<=0 时按 1 处理。
    """
    base = prompt_cfg.prompt
    count = prompt_cfg.effective_count
    perts = prompt_cfg.perturbations

    out: list[str] = []
    for i in range(count):
        if perts:
            pert = perts[i % len(perts)]
            out.append(f"{base}\n{pert}")
        else:
            out.append(base)
    return out
```

`src/models/__init__.py` 加一行：

```python
from models.prompt_config import PromptConfig, build_prompts
```

---

### 4.3 `src/dingtalk/client.py`

> 现有 `get_record` / `update_record` / `upload_attachment` 已经够用（`TableConfig` 本身就有 `base_id` / `sheet_id`），批量流程直接复用，不新增 `*_by_ids` 系列方法。

#### 4.3.1 新增 `list_records`

```python
async def list_records(
    self,
    base_id: str,
    sheet_id: str,
    field: str,
    value: str,
    field_names: list[str] | None = None,
    max_results: int = 100,
    next_token: str | None = None,
) -> list[dict]:
    """按字段精确过滤查询。

    对应钉钉 SDK: list_records_with_options_async
    详见 docs/sdk_docs/列出多行记录.md

    Returns:
        [{id, fields, ...}, ...]；空列表 = 未找到。
    """
    async def _do():
        token = await self._get_access_token()
        headers = notable_models.ListRecordsHeaders()
        headers.x_acs_dingtalk_access_token = token

        request = notable_models.ListRecordsRequest(
            operator_id=self.operator_id,
            max_results=max_results,
            next_token=next_token,
            filter={
                "combination": "and",
                "conditions": [{
                    "field": field,
                    "operator": "equal",
                    "value": [value],
                }],
            },
            field_id_or_names=field_names,
        )
        response = await self._client.list_records_with_options_async(
            base_id=base_id,
            sheet_id_or_name=sheet_id,
            request=request,
            headers=headers,
            runtime=util_models.RuntimeOptions(),
        )
        return response.body.records if hasattr(response, "body") else []

    return await self._retry_on_network_error(_do)
```

---

### 4.4 `src/generator/engine.py`

#### 4.4.1 `AIGenerator` 新增 `generate_batch`

```python
    # 批量生图内部并发上限，避免 count 较大时打爆上游 API rate limit
    _BATCH_CONCURRENCY = 3

    async def generate_batch(
        self,
        model: str,
        prompts: list[str],
        reference_image: bytes | None = None,
        table_config: TableConfig | None = None,
    ) -> list[bytes | None]:
        """并发调 generate() 生成多张图，单张失败转 None 不抛。

        API Key 通过 table_config.image_api_key_env 解析。
        内部用 Semaphore 限制并发数（_BATCH_CONCURRENCY）。
        """
        if not prompts:
            return []

        semaphore = asyncio.Semaphore(self._BATCH_CONCURRENCY)

        async def _one(idx: int, prompt: str) -> bytes | None:
            try:
                async with semaphore:
                    return await self.generate(
                        model=model, prompt=prompt,
                        reference_image=reference_image,
                        table_config=table_config,
                    )
            except Exception as e:
                logger.warning("单图生成失败", index=idx, error=str(e))
                return None

        results = await asyncio.gather(*[_one(i, p) for i, p in enumerate(prompts)])
        return list(results)
```

现有 `generate()` 方法**完全不动**。

---

### 4.5 `src/services/generation.py`

#### 4.5.0 抽公共方法 `_resolve_model`

单图与批量都需从 `fields[model_field]` 解析出 model 字符串（处理 str / dict / 缺失三种情况），抽成静态方法复用：

```python
@staticmethod
def _resolve_model(fields: dict, table_config: TableConfig, default: str) -> str:
    """从 fields[model_field] 解析出模型名称，缺失则回退 default。"""
    raw = fields.get(table_config.model_field)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("name", default)
    return default
```

#### 4.5.1 `process()` 加分支

```python
async def process(self, record_id: str, table_key: str | None = None) -> None:
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
```

#### 4.5.2 抽 `_process_single`

把现有 `process()` 主体逻辑搬到 `_process_single(self, record_id, table_config)`。修改点：

- **模型解析（第 5 步）**：改用 `_resolve_model` 静态方法，与 `_process_batch` 对齐。
- **其余代码**：原样保留，仅缩进一层 + 每步加 `step = "..."` 标签。

> 原 `process()` 第 81 行 `step = "开始"` 在新版 `process()`（§4.5.1）里不再出现，重构后自然消失，无需单独处理。

#### 4.5.3 新增 `_process_batch`

```python
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
        prompt_records[0].get("fields", {}), table_config.prompt_table
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
    for i, img_bytes in enumerate(results):
        if img_bytes is None:
            continue
        try:
            att = await self.dingtalk.upload_attachment(
                table_config,
                img_bytes,
                f"generated_{record_id}_{i+1}.png",
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
```

#### 4.5.4 顶部新增导入

```python
from models.prompt_config import PromptConfig, build_prompts
```

---

## 5. 测试矩阵

### 5.1 `tests/test_models/test_prompt_config.py`（新增）

| 用例 | 输入 | 期望 |
|------|------|------|
| 标准场景 | count=3, perts=[a,b,c] | 3 prompts，扰动对齐 |
| 扰动不足 | count=3, perts=[a] | 3 prompts，全用 a |
| 无扰动 | count=3, perts=[] | 3 prompts，全是 base |
| count=0 / None / "" / "invalid" | 异常值 | effective_count=1 |
| 钉钉富文本 | `[{text: "..."}]` | 正确解析 |
| 扰动带空行 | `"a\n\nb\n"` | `["a", "b"]` |

### 5.2 `tests/test_dingtalk/test_client.py`

**保留现有所有单测**（确保无回归）。

**新增**：

| 用例 | 期望 |
|------|------|
| `list_records` 单条匹配 | 返回 `[{id, fields}, ...]` |
| `list_records` 无匹配 | 返回 `[]` |
| `list_records` 多条匹配 | 返回 N 条 |
| `list_records` 网络异常 | 重试后抛出 |
| `list_records` 参数透传 | request.filter 包含 field/operator/value |

### 5.3 `tests/test_generator/test_engine.py`（新增）

| 用例 | 期望 |
|------|------|
| 空 prompts | 返回 `[]` |
| 全成功 | 顺序与 prompts 一致 |
| 单张失败 | 返回 `[None, b, None]` |
| 内部并发限流 | N 张 prompt 实际并发 ≤ `_BATCH_CONCURRENCY`（3） |

### 5.4 `tests/test_services/test_generation.py`

**保留现有 6 个单图用例**（确保无回归）。

**新增批量分支用例**：

| 用例 | 关键 mock | 期望 |
|------|-----------|------|
| 完整批量成功 | `get_record` → 含素材图/模型；`list_records` → 含提示词/数量=3；`generate_batch` → 3 bytes | `upload_attachment` × 3，`update_record` 收到 3 个 attachment |
| 单图失败 | `generate_batch` → `[b, None, b]` | upload × 2，状态"成功" |
| 全失败 | `generate_batch` → `[None, None]` | upload × 0，状态"失败" |
| 素材图缺失 | fields 无 reference_image | 失败"素材图不能为空" |
| 提示词表无匹配 | `list_records` → `[]` | 失败"提示词表未找到 任务名称=X" |
| 提示词为空 | 找到但 prompt 字段空 | 失败"提示词表的提示词为空" |
| 扰动复用 | perts=[a], count=3 | 3 prompt 都含 `\na` |
| task_name 来自配置 | 验证传给 `list_records` 的 value == `table_config.task_name` | 不读 fields |
| batch_mode=false | `table_config.batch_mode = false` | 调用 `_process_single` |

### 5.5 配置加载测试

| 用例 | 期望 |
|------|------|
| 现有 config.toml（无 batch_mode）加载 | 成功，新字段全默认 |
| `batch_mode=true` 但缺 `task_name` | 启动失败，提示明确 |
| `batch_mode=true` 但缺 `prompt_table_sheet_id` | 启动失败，提示明确 |
| `batch_mode=true` 配置完整 | 加载成功 |

---

## 6. 兼容性 & 迁移

### 6.1 零破坏

| 项 | 影响 |
|----|------|
| 现有 3 张表 `[[dingtalk.tables]]` 配置 | 完全不动 |
| 现有 HTTP 接口 `/api/v1/generate` | 完全不动 |
| 现有 `GenerationService` 单图流程 | 逻辑搬到 `_process_single`，签名不变 |
| 现有 `DingTalkClient.get_record(table_config, ...)` 等 | 保留，内部走底层 |
| 现有 `AIGenerator.generate()` | 完全不动 |
| 现有钉钉表格数据 | 完全不动 |
| **批量模式状态文本** | 新增 `"成功 N/M"` 形式（部分成功场景）；单图流程仍只产 `"成功"` 或 `"失败: xxx"` |
| **流程日志 step 标签** | 重命名（如 `step="开始"` → `step="初始化"`/`"获取表格配置"`）。如有外部告警/审计依赖旧 step 字面值，需同步更新正则 |

### 6.2 钉钉侧 SOP

1. 建新 base（推荐）或在现有 base 下：
   - 新建 sheet「提示词配置」+ 6 个字段（任务名称、提示词、生成类型、生成数量、生成比例、分辨率、扰动列表）
2. 为每种任务建 1 张生图表 sheet（如"动作图-A"、"姿态图-A" 各一张）
3. 每张生图表字段：素材图、生图模型、生成图片、生成结果、生成时间、运行情况、款式编码、运行账号（按需）
4. 录入提示词配置到提示词表

### 6.3 配置侧 SOP

5. 在 config.toml 加 `[[dingtalk.tables]]` 段（与现有 3 段平级）
6. 段内加 `batch_mode = true` + `task_name = "动作图-A"` + `prompt_table_sheet_id = "..."`
7. 同段加 `[dingtalk.tables.prompt_table]` 子段
8. 重启服务

### 6.4 钉钉自动化侧 SOP

9. 把触发 URL 的 `table_key` 从 `zhuozhi-base` 改成 `zhuozhi-batch-action`
10. 其他什么都不用改

---

## 7. 验证步骤

```bash
cd F:\MyCode\PythonProject\ai-image-service

# 1. 配置加载冒烟
& "D:\miniconda\envs\spider\python.exe" -c "from config import get_settings; s = get_settings(); t = s.get_table('zhuozhi-base'); print('batch_mode:', t.batch_mode); t = s.get_table('zhuozhi-batch-action'); print('batch_mode:', t.batch_mode, 'task_name:', t.task_name, 'prompt_table_sheet:', t.prompt_table_sheet_id)"

# 2. 跑全部测试（含回归）
& "D:\miniconda\envs\spider\python.exe" -m pytest tests/ -v

# 3. 静态检查
& "D:\miniconda\envs\spider\python.exe" -m ruff check src/

# 4. 启动服务
& "D:\miniconda\envs\spider\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8030

# 5. 旧接口回归
curl -X POST http://localhost:8030/api/v1/generate \
  -H "Authorization: Bearer xxx" \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recOld", "table_key": "zhuozhi-base"}'

# 6. 新接口（批量）
curl -X POST http://localhost:8030/api/v1/generate \
  -H "Authorization: Bearer xxx" \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recNew", "table_key": "zhuozhi-batch-action"}'
```

**成功判据**：

- 旧 `table_key="zhuozhi-base"` 行为完全不变（1 张图）
- 新 `table_key="zhuozhi-batch-action"` count=3 时附件字段出现 3 张图
- 提示词表扰动列表正确反映到 prompt（日志可见）
- `task_name="动作图-A"` 在提示词表里找不到 → 失败"提示词表未找到 任务名称=动作图-A"
- `count=0` → 实际生 1 张图（兜底）
- `batch_mode=true` 但缺 `task_name` → 启动失败

---

## 8. 风险与未决项

| 风险 | 缓解 |
|------|------|
| 钉钉 SDK `list_records_with_options_async` filter 字段值类型 | SDK 自动转换；单测验证 |
| `TableConfig` 字段变胖 | 启动时校验完整性，缺则 fail-fast |
| `DingTalkClient` 抽底层方法 | 充分回归测试；旧方法签名不变 |
| 多 sheet 重复配置 `prompt_table` 子段 | 当前接受；后续可考虑全局共享段 |
| `list_records` 单页 100 条上限 | 提示词表 < 100 时无需分页；未来需要时补 `next_token` 循环 |
| 提示词表里"任务名称"列名硬编码 | 钉钉侧保持字面量"任务名称"即可；如需改字段名后续再加配置项 |

---

## 9. 不在本期范围

- 全局共享 `[dingtalk.prompt_table]` 配置段
- 真正消费 `aspect_ratio` / `resolution`（保留配置位）
- 多素材图
- 本地存储
- 视频生成的批量
- 提示词表的版本管理 / 变更审计
- HTTP 批量接收多条 `record_id`
- 主动拉取表格中"待处理"记录
- `list_records` 分页循环
- 提示词表"任务名称"列名做成配置项

---

## 10. 实施顺序

1. `src/config.py`：`PromptTableConfig` + `TableConfig` 加字段 + `_validate_batch_mode_config` + 加载逻辑
2. `src/dingtalk/client.py`：仅新增 `list_records`；现有方法不动
3. `src/models/prompt_config.py`：`PromptConfig` + `build_prompts`（含 `models/__init__.py` 导出）
4. `src/generator/engine.py`：`generate_batch` + `api_key_env` 可选参数
5. `src/services/generation.py`：`process` 分流 + `_process_single` 重命名 + `_process_batch` 新增
6. 测试：5 个测试文件的新增/修改用例
7. 钉钉端到端冒烟
8. `docs/ROADMAP.md` 更新

每步独立可回滚；任何一步失败不影响现有 3 张表的服务。