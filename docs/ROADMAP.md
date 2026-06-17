# 项目计划

## 目标

实现钉钉AI表格驱动的图片生成后端服务。

核心流程：用户点击"生成按钮" → 钉钉自动化触发 → 后端接收请求 → 获取钉钉记录（素材图、提示词、模型）→ 调用 AI 生图 → 回写结果到钉钉表格。

---

## 一、项目骨架

- FastAPI 应用入口（main.py）
- 配置管理（pydantic-settings 从 .env 加载敏感信息 + tomllib 从 config.toml 加载非敏感配置）
- 日志配置（loguru，带 task_type、record_id 上下文）
- 项目结构（src/ 下直接放置 main.py、config.py 及 api/、services/、dingtalk/、generator/、models/ 子包）

---

## 二、钉钉 SDK 客户端

使用 `alibabacloud-dingtalk` SDK（`alibabacloud_dingtalk.notable_1_0` 子模块）。

**配置文件**：
- `.env`（敏感）：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_ID`、`API_KEY`、`ZHUOZHI_IMAGE_API_KEY`、`AHMI_IMAGE_API_KEY`、`HUAPU_IMAGE_API_KEY`
- `config.toml`（非敏感）：多套 AI 表格配置（见下方）

**多表格配置方案**：

```toml
[dingtalk]
default_table = "clothing"  # 默认使用的表格 key

[[dingtalk.tables]]
key = "clothing"
base_id = "tbl_xxx"
sheet_id = "sheet_xxx"
# 字段映射（代码中不硬编码任何字段名）
prompt_field = "提示词"
model_field = "生图模型"
reference_image_field = "素材图"
result_image_field = "生成图片"
result_status_field = "生成结果"
result_time_field = "生成时间"

[[dingtalk.tables]]
key = "poster"
base_id = "tbl_yyy"
sheet_id = "sheet_yyy"
prompt_field = "Prompt"
model_field = "Model"
reference_image_field = "Reference"
result_image_field = "Output"
result_status_field = "Status"
result_time_field = "Time"
```

**说明**：
- 每个表格通过 `key` 区分，请求时传入 `table_key` 选择（可选，默认使用 `default_table`）
- 每个表格独立定义字段映射，代码中不硬编码任何字段名
- `operator_id` 全局一个，在 `.env` 中配置，不区分表格
- 新增表格只需加配置，不用改代码

**API 端点**：

| 操作 | API | 说明 |
|------|-----|------|
| 获取 AccessToken | `POST /v1.0/oauth2/accessToken` | AppKey + AppSecret |
| 获取记录 | `GET /v1.0/notable/bases/{baseId}/sheets/{sheetIdOrName}/records/{recordId}` | 需要 operatorId |
| 更新记录 | `PUT /v1.0/notable/bases/{baseId}/sheets/{sheetIdOrName}/records` | 附件格式参考 `docs/sdk_docs/上传附件.md` |
| 上传附件 | 3步：获取上传信息 → PUT到uploadUrl → 写入记录 | 详见 `docs/sdk_docs/上传附件.md` |

**功能**：
- **Token 管理**：缓存 + 自动刷新
- **记录读取**：get_record
- **记录更新**：update_records
- **附件上传**：获取上传信息 → PUT上传 → 写入记录（参考 `docs/sdk_docs/上传附件.md`）
- **素材图下载**：httpx.get() + access token header（参考 `docs/sdk_docs/SDK实现指南.md` 第6节）

关键模块：
- `dingtalk/client.py` — DingTalkClient 封装所有 API
- 无 `dingtalk/models.py` — 记录直接用原始 dict 操作（字段名动态，不做静态模型）

---

## 三、AI 生图引擎

AIGenerator 统一入口，根据 model 路由到对应 SDK Client。

- **重试机制**：使用 `tenacity` 库，仅对网络异常重试（`httpx.ConnectError`、`httpx.TimeoutException`、`httpx.NetworkError`），各网络调用方法独立添加 `@retry_on_network_error` 装饰器（`DingTalkClient.*`、`AIGenerator.generate()`），`process()` 本身不做重试
- **并发控制**：Semaphore，max_concurrency = 5（可配置），超出时排队等待（无超时）
- **图生图**：reference_image bytes 作为输入，素材图必填
- **输出格式**：统一 PNG

**模型名称映射**（从 config.toml [ai.model.*] 读取，代码不硬编码）：

| 钉钉表格值 | 真实 model_name（传给 SDK） | provider | 中转站 endpoint |
|------------|----------------------------|----------|-----------------|
| `Nano Banana Pro` | `gemini-3-pro-image-preview` | google | /v1beta/models/...:generateContent |
| `Nano Banana 2` | `gemini-3.1-flash-image-preview` | google | /v1beta/models/...:generateContent |
| `GPT Image 2` | `gpt-image-2` | openai | /v1/images/edits |

所有模型共用中转站 base_url: https://api.vectorengine.ai

关键模块：
- `generator/engine.py` — AIGenerator（统一入口）

---

## 四、生图编排服务

参考旧项目的 TaskRunner 设计：

- 完整流程：获取记录 → 校验字段 → 下载素材图 → 调用AI生图 → 上传结果 → 回写表格
- **字段校验**：提示词必填、素材图必填，空了回写 `"失败: 提示词不能为空"` / `"失败: 素材图不能为空"`
- **错误处理**：失败时回写"失败: {str(e)}"，完整 traceback 仅写 loguru 日志
- **并发控制**：Semaphore 限制最大并发数（默认 5），放置在 GenerationService 层，全局限制，超出时排队等待
- **重试机制**：不在 process() 层面重试，各网络调用方法（DingTalkClient.*、AIGenerator.generate()）各自通过 @retry_on_network_error 独立处理，仅网络异常重试
- **日期格式**：生成时间字段写入格式为 `"YYYY-MM-DD HH:mm"` 字符串

关键模块：
- `services/generation.py` — GenerationService 编排整个流程

---

## 五、API 接口

- `POST /api/v1/generate` — 接收钉钉自动化回调，触发异步生图（返回 202 Accepted）
- `GET /api/v1/health` — 健康检查（返回 `{"status": "ok", "version": "0.1.0"}`）

**错误码**：

| 状态码 | 场景 |
|--------|------|
| 400 | 参数校验失败（缺少 record_id） |
| 401 | API Key 无效 |
| 202 Accepted | 任务已接收，后台处理中 |

**错误处理策略**：
- **同步校验错误**（参数格式、API Key 无效）→ 立即返回 4xx
- **异步业务错误**（记录不存在、AI 失败、下载失败等）→ 统一回写表格"失败: xxx"，HTTP 始终返回 202 Accepted

---

## 六、端到端验证

- 钉钉表格字段配置说明
- 自动化规则配置（触发器、HTTP 请求）
- 本地调试方法（curl 模拟回调）

---

## 执行顺序

1. 项目骨架（config.py、main.py、依赖）
2. 钉钉 SDK 客户端
3. AI 生图引擎
4. 生图编排服务
5. API 接口
6. 端到端验证

---

## 七、视频生成（已实现 ✅）

### 7.1 业务背景

在图片生成基础上新增**完全独立的视频生成能力**（独立接口、独立编排、独立 generator、独立配置）。首批接入三家厂商：快手可灵 (Kling) / 海螺 (Hailuo) / 通义万象 (Wanxiang)，均通过 `https://api.vectorengine.ai` 中转。

### 7.2 已完成功能

- ✅ 配置层：`VideoProviderConfig` / `VideoTableConfig` / `VideoPollConfig` + toml 加载 + 环境变量覆盖
- ✅ `VideoGenerator`：三家 `_submit_*` / `_poll_*` + 统一 `_poll_until_done` 骨架 + `_download_video`
- ✅ `_resolve_provider` 前缀路由（`kling*` / `MiniMax-Hailuo*` / `happyhorse*` 等）
- ✅ `VideoGenerationService`：9 步编排（步骤 5-7 在 generator 内完成）+ `Semaphore(video_max_concurrency=3)`
- ✅ `POST /api/v1/video/generate` 路由 + `VideoGenerateRequest` 模型
- ✅ `upload_attachment` 加 `media_type` 入参（视频场景 `video/mp4`）
- ✅ 单元测试：`tests/test_generator/test_video_engine.py`（8 个 case，覆盖三家 submit/poll/失败/超时）+ `tests/test_services/test_video_generation.py`（6 个 case，覆盖 9 步流程）+ API 路由测试 4 个 case
- ✅ 文档：ARCHITECTURE.md §9、MODULES.md §9、API.md §6、本文件 §7

### 7.3 已知限制 / 待用户填值

- `config.toml` 中三个 `[[dingtalk.video_tables]]` 的 `base_id` / `sheet_id` 为 `<待补>`，需要用户填入钉钉视频表的实际 ID 后才能端到端运行
- **视频复用同品牌图片 API Key**（vectorengine 中转站同一账号 image/video 通用）：`config.toml` 中 `video_api_key_env` 直接指向 `ZHUOZHI_IMAGE_API_KEY` / `HUAPU_IMAGE_API_KEY` / `AHMI_IMAGE_API_KEY`，无需新增 `*_VIDEO_API_KEY`
- 海螺/通义万象对 base64 的支持度需实测：当前按 base64 实现，若实测不支持则需切换为"先回写钉钉云空间获得公网 URL"作为兜底（当前未预留）

### 7.4 后续可扩展

- 文生视频（text-to-video）：当前仅图生视频（image-to-video）
- 多镜头视频拼接
- 历史任务查询接口（按 record_id 查询当前任务进度）
- 视频任务主动回调（当前走轮询，可改为 webhook）

---

## 八、批量生图（已实现 ✅）

### 8.1 业务背景

在现有单图生图基础上新增**批量生图模式**：一次 HTTP 触发，基于双表（生图表 + 提示词表）生成多张图片（同一提示词模板 × 不同扰动），串行上传后回写。现有 3 张单图配置段、HTTP 接口、单图流程全部不动；通过 `TableConfig.batch_mode` 字段分流。

### 8.2 已完成功能

- ✅ `PromptTableConfig`：提示词表字段映射（提示词 / 生成类型 / 生成数量 / 生成比例 / 分辨率 / 扰动列表）
- ✅ `TableConfig` 扩展：`batch_mode` / `task_name` / `prompt_table_sheet_id` / `prompt_table` + 可选增强字段（`error_field` / `style_code_field` / `run_account_field`）
- ✅ `_validate_batch_mode_config()`：启动时校验，缺字段即 fail-fast
- ✅ `models/prompt_config.py`：`PromptConfig.from_prompt_record` + `build_prompts`（扰动按索引对齐 + 循环复用 + count≤0 兜底）
- ✅ `DingTalkClient.list_records`：按字段精确过滤，对应 SDK `list_records_with_options_async`，支持 `task_name` → 提示词表行查询
- ✅ `AIGenerator.generate_batch`：并发调 `generate()`，单张失败转 `None` 不抛；内部 `Semaphore(_BATCH_CONCURRENCY=3)` 限流
- ✅ `GenerationService` 重构：`process()` 加 `batch_mode` 分支；现有逻辑搬到 `_process_single()`；新增 `_process_batch()`（双表查询 + 多图生成 + 串行上传 + 回写）；抽 `_resolve_model` 静态方法复用
- ✅ 单元测试：`tests/test_models/test_prompt_config.py`（10 个）+ `tests/test_generator/test_engine.py`（5 个）+ `tests/test_dingtalk/test_client.py` 新增 5 个 `list_records` 用例 + `tests/test_services/test_generation.py` 新增 13 个（9 批量流程 + 4 配置校验）
- ✅ HTTP 接口 `POST /api/v1/generate {record_id, table_key}` 完全不变；调用方把 `table_key` 换成批量表 key 即可

### 8.3 配置示例（详见 `docs/PLAN-batch-image-generation.md` §4.1.5）

批量表**复用现有品牌的 `*_IMAGE_API_KEY`**（同一中转站账号），无需新增 `BATCH_*` 类 key：

```toml
[[dingtalk.tables]]
key = "zhuozhi-batch-action"
base_id = "<新建 base 的 id>"
sheet_id = "<动作图 sheet_id>"
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"  # 复用现有 Key

batch_mode = true
task_name = "动作图-A"
prompt_table_sheet_id = "<提示词表 sheet_id>"

[dingtalk.tables.prompt_table]
prompt_field = "提示词"
count_field = "生成数量"
perturbations_field = "扰动列表"
# ... 其他字段同理
```

### 8.4 已知限制 / 待用户填值

- 与视频生成共用同一钉钉 base：批量表的 `base_id` / `sheet_id` 需用户填实际值；提示词表的 `任务名称` 列名硬编码为字面量"任务名称"
- 同一 sheet 内所有行共用同一个 `task_name`（写在配置里），跨任务需拆 sheet
- 提示词表行 ≤ 100 时无需分页（`max_results` 默认 100）；超过需补 `next_token` 循环
- 批量模式状态文本新增 `"成功 N/M"` 形式（部分成功场景）；单图流程仍只产 `"成功"` 或 `"失败: xxx"`
- `aspect_ratio` / `resolution` 字段保留配置位但当前未真正消费

### 8.5 后续可扩展（不在本期范围）

- 全局共享 `[dingtalk.prompt_table]` 配置段（多 sheet 重复配置当前接受）
- 真正消费 `aspect_ratio` / `resolution`
- 多素材图
- HTTP 批量接收多条 `record_id`
- 主动拉取表格中"待处理"记录
- `list_records` 分页循环
- 提示词表"任务名称"列名做成配置项