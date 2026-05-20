# 系统架构设计

## 1. 概述

本项目是一个**钉钉AI表格驱动的图片生成服务**，核心流程如下：

```
┌─────────────────────────────────────────────────────────────────┐
│                        钉钉AI表格（前端）                         │
│  字段：编号 | 款号 | 素材图 | 提示词 | 生图模型 |                │
│        生成图片 | 生成结果 | 生成时间 | 生成按钮                  │
│                                                                  │
│  1. 协作者填写款号、提示词、选择生图模型、上传素材图             │
│  2. 点击"生成按钮"                                               │
│  3. 钉钉自动化流程 → HTTP POST 记录ID                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP POST /api/v1/generate
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Python 后端服务                             │
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────┐      │
│  │  API层   │───▶│  业务服务层   │───▶│  钉钉集成层       │      │
│  │ (FastAPI)│    │ (services/)  │    │  (dingtalk/)      │      │
│  └──────────┘    └──────┬───────┘    └───────────────────┘      │
│                         │                                        │
│                         ▼                                        │
│                  ┌──────────────┐                                │
│                  │  AI生图引擎   │                                │
│                  │ (generator/)  │                                │
│                  │  - NanoBanana │                                │
│                  │  - GPT-Image  │                                │
│                  └──────────────┘                                │
│                                                                  │
│  4. 接收记录ID → 5. 钉钉SDK获取记录数据                          │
│  6. 下载素材图 → 7. 调用AI生图（图生图 img2img）                 │
│  8. 回写结果到钉钉表格（生成图片、生成结果、生成时间）            │
└─────────────────────────────────────────────────────────────────┘
```

## 2. 技术选型

| 组件 | 技术 | 选型理由 |
|------|------|----------|
| Web 框架 | FastAPI | 异步、自动OpenAPI文档、类型安全 |
| ASGI 服务器 | Uvicorn | 轻量、高性能 |
| 钉钉 SDK | alibabacloud-dingtalk | 官方 SDK，多维表格读写、文件上传下载，**全程使用 `*_async` 方法** |
| HTTP 客户端 | httpx | 异步、支持连接池、调用中转站 API |
| 数据验证 | Pydantic v2 | 类型校验、序列化 |
| 图片处理 | Pillow | 格式转换、尺寸调整 |
| AI 生图 | NanoBanana (Google SDK) / GPT-Image (OpenAI SDK) | 通过中转站调用 |
| 任务队列 | asyncio 后台任务 | 生图耗时，需异步，**禁止使用 Celery** |
| 配置管理 | pydantic-settings | 环境变量/配置文件 |
| 日志 | loguru | 结构化日志、更友好 |
| 测试 | pytest + pytest-asyncio | 标准异步测试框架 |

## 3. 目录结构

```
ai_gen_image/              # 项目根目录
├── CLAUDE.md              # AI 上下文文档
├── pyproject.toml         # 项目元数据与依赖
├── configs/
│   ├── .env.example       # 环境变量模板（敏感信息）
│   └── config.toml        # 非敏感配置
├── docs/
│   ├── ARCHITECTURE.md    # 架构设计（本文件）
│   ├── API.md             # 接口文档
│   ├── MODULES.md         # 模块设计
│   ├── WORKFLOW.md        # 开发工作流
│   ├── ROADMAP.md         # 迭代路线图

├── src/                   # 源代码
│   ├── main.py            # FastAPI 应用入口
│   ├── config.py          # 配置管理
│   ├── api/               # API 层 → 接收请求、参数校验、路由
│   │   ├── __init__.py
│   │   ├── router.py      #   FastAPI Router
│   │   └── deps.py        #   依赖注入
│   ├── services/          # 业务编排层 → 生图主流程
│   │   ├── __init__.py
│   │   └── generation.py  #   GenerationService
│   ├── dingtalk/          # 钉钉集成层 → 封装钉钉 Python SDK
│   │   ├── __init__.py
│   │   └── client.py      #   DingTalkClient（记录用原始 dict 操作）
│   ├── generator/         # AI生图引擎层 → 按 model 分派到 Google / OpenAI SDK
│   │   ├── __init__.py    #   包入口，re-export AIGenerator
│   │   └── engine.py      #   AIGenerator（统一入口）
│   └── models/            # 通用数据模型
│       ├── __init__.py
│       ├── request.py     #   GenerateRequest
│       └── response.py    #   TaskResponse
└── tests/
    ├── __init__.py
    ├── conftest.py         # pytest fixtures
    ├── test_api/
    ├── test_services/
    └── test_dingtalk/
```

## 4. 数据流详解

### 4.1 触发流程

```
用户点击"生成按钮"
  → 钉钉表格自动化规则匹配到该事件
  → 自动化动作：发送 HTTP 请求
  → POST /api/v1/generate  { "record_id": "recXXXXXX" }
```

### 4.2 后端处理流程

```
FastAPI接收请求 → 放入后台任务（立即返回 202 Accepted）
  │
  ├─ 1. 通过钉钉SDK获取记录详情
  │     → 拿到：素材图URL、提示词、生图模型
  │
  ├─ 2. 下载素材图（如果存在）
  │     httpx.get(素材图URL) → 本地临时文件或内存 bytes
  │
  ├─ 3. 调用AI生图引擎
  │     generator.generate(model, prompt, reference_image_bytes)
  │     → 返回生成图片的字节流
  │
  ├─ 4. 上传生成图片到钉钉云空间
  │     钉钉 SDK 上传文件接口
  │     → 拿到生成图片的file_id/URL
  │
  └─ 5. 更新钉钉表格记录
        → 写入：生成图片URL、生成结果="成功"、生成时间
```

### 4.3 错误处理流程

```
同步校验错误（参数格式、API Key） → 立即返回 4xx
异步业务错误（任一步骤失败） → 更新钉钉记录"生成结果"字段为错误信息
  → "失败: {error_message}"
  → 同时写入"生成时间"标记处理时间点
  → HTTP 始终返回 202 Accepted
```

## 5. AI 生图引擎

### 5.1 支持的模型

| 模型 | SDK | base_url |
|------|-----|----------|
| Nano Banana Pro | Google genai SDK | 每个模型独立配置 |
| Nano Banana 2 | Google genai SDK | 每个模型独立配置 |
| GPT Image 2 | OpenAI SDK | 每个模型独立配置 |

### 5.2 图生图（img2img）流程

```
素材图（bytes） + 提示词（str）
    ↓
AI 模型处理
    ↓
生成新图片（bytes）→ 统一转为 PNG 格式
```

**模型调用细节**：
- **Google genai SDK**（NanoBanana）：SDK 自动追加 `v1beta/models/{model}:generateContent` 路径。`base_url` 配置为网关根地址即可。
- **OpenAI SDK**（GPT-Image）：SDK 自动追加 `/images/edits` 路径。`base_url` 需包含 `/v1` 前缀（如 `https://api.vectorengine.ai/v1`）。

**输出格式统一**：无论模型返回什么格式，在 `AIGenerator.generate()` 出口用 Pillow 统一转 PNG：
```python
from PIL import Image
import io

def _to_png_bytes(image_bytes: bytes) -> bytes:
    """将任意格式图片字节统一转为 PNG 格式。"""
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
```

## 6. 关键设计决策

### 6.1 为什么用后台任务而非同步返回？

AI生图通常耗时 5-60 秒，钉钉自动化回调有超时限制（通常 5 秒）。后端必须在收到请求后**立即返回**，然后异步处理。返回 202 Accepted，后续通过回写表格来通知结果。

**注意**：HTTP 响应在 `background_tasks.add_task()` 后立即返回，后台任务在事件循环中排队执行，不受钉钉回调超时影响。

### 6.2 为什么通过记录ID获取数据而不是请求体传全部数据？

钉钉自动化的 HTTP 请求体大小有限制。只传记录ID，后端通过SDK去拉取完整数据，更可靠、更安全。`base_id` 和 `sheet_id` 写死在配置文件中，请求体只需传 `record_id` 和可选的 `table_key`。

### 6.5 多表格支持

系统支持同时服务多个不同的 AI 表格场景（如服装生图、海报生图等），每个表格通过 `table_key` 区分。

- **配置驱动**：每个表格的 `base_id`、`sheet_id`、字段名映射全部在 `config.toml` 中通过 `[[dingtalk.tables]]` 数组定义
- **代码不硬编码**：所有字段名从 `TableConfig` 读取，不写死"提示词"、"素材图"等字符串
- **扩展简单**：新增表格只需加配置，不用改代码
- **operator_id 全局**：所有表格共用一个操作人 unionId，在 `.env` 中配置
- **DingTalkClient 设计**：Client 初始化只绑定 `app_key`、`app_secret`、`operator_id`（全局共享），`base_id`/`sheet_id` 在方法级别通过 `TableConfig` 传入

### 6.3 生成结果如何回传？

通过钉钉多维表格API，直接写入"生成图片"附件字段和"生成结果"文本字段。"生成时间"字段在更新时一并写入。

**错误回写规则**：
- 业务执行中任何步骤失败 → 回写 `"失败: {str(e)}"`
- 完整 traceback 仅写入 loguru 日志，不回写表格
- HTTP 始终返回 202 Accepted（只要请求格式正确 + 鉴权通过）

### 6.4 多模型路由

AIGenerator 根据 model 参数（钉钉表格"生图模型"字段值）自动路由到对应 SDK。

- `Nano Banana Pro` / `Nano Banana 2` → Google genai SDK
- `GPT Image 2` → OpenAI SDK

**模型映射表**（从 `config.toml [ai.model.*]` 读取，代码不硬编码）：

| 钉钉表格值 | provider | 真实 model_name | base_url |
|------------|----------|-----------------|----------|
| `Nano Banana Pro` | google | `gemini-3-pro-image-preview` | `https://api.vectorengine.ai` |
| `Nano Banana 2` | google | `gemini-3.1-flash-image-preview` | `https://api.vectorengine.ai` |
| `GPT Image 2` | openai | `gpt-image-2` | `https://api.vectorengine.ai/v1` |

每个模型独立配置 `base_url`，SDK 自动拼接各自 API 路径，无需手动拼接 endpoint。

## 7. 安全考虑

- **API鉴权**：需实现一个简单的 token 验证，防止未授权调用
- **钉钉凭证**：AppKey/AppSecret 通过环境变量注入，不写入代码
- **AI API Key**：NanoBanana 系列（Pro / 2）共用一个 key，GPT Image 2 独立一个 key，均通过中转站调用。各模型 `base_url` 在 `config.toml` 中独立配置
- **文件隔离**：素材图下载后处理，避免路径穿越
- **速率限制**：通过 `asyncio.Semaphore` 限制最大并发生图数（默认 5），超出时排队等待

> **并发控制说明**：Semaphore 在单 worker 下有效。生产部署时仅启动 1 个 worker（`uvicorn` 不带 `--workers` 参数），无需进程间同步。如果未来切多 worker，需要引入外部信号量（如 Redis 或文件锁）。

## 8. 部署架构（初期）

```
单机部署：
  Uvicorn (多 worker)
    ├── FastAPI 主进程
    └── asyncio 后台任务（Semaphore 限流 + 各网络方法独立 tenacity 重试）

后期可扩展为：
  FastAPI (接收请求) → Redis (消息队列) → Celery Worker (处理生图)
```
