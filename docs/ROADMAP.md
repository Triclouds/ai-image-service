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
- `.env`（敏感）：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_ID`、`API_KEY`、`ZHUOZHI_NANOBANANA_API_KEY`、`ZHUOZHI_GPT_IMAGE_API_KEY`、`AHMI_NANOBANANA_API_KEY`、`AHMI_GPT_IMAGE_API_KEY`、`HUAPU_NANOBANANA_API_KEY`、`HUAPU_GPT_IMAGE_API_KEY`
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