# AI Gen Image

## 项目概述

**钉钉AI表格驱动的图片生成后端服务**。

协作者在钉钉多维表格中填写款号、提示词、选择生图模型、上传素材图，点击"生成按钮"触发自动化流程 → HTTP 回调本服务 → 服务获取钉钉记录数据 → 调用 AI 模型生成图片（图生图） → 回写结果到钉钉表格。

## 技术栈

- **Runtime**: Python 3.11+（conda: `D:\miniconda\envs\spider\python.exe`）
- **Web框架**: FastAPI + Uvicorn
- **HTTP客户端**: httpx（异步）
- **数据模型**: Pydantic v2 + pydantic-settings
- **图片处理**: Pillow
- **日志**: loguru
- **测试**: pytest + pytest-asyncio
- **代码质量**: ruff + mypy

## AI 模型

本服务支持以下 AI 图片生成模型（均通过中转站调用）：

| 模型 | SDK | 用途 |
|------|-----|------|
| Nano Banana Pro / Nano Banana 2 | Google SDK | 图生图、文生图 |
| GPT Image 2 | OpenAI SDK | 图生图、文生图 |

## 项目结构

```
ai_gen_image/
├── main.py              # FastAPI 应用入口
├── config.py            # 配置管理 (pydantic-settings)
├── api/
│   ├── router.py        # API 路由（POST /api/v1/generate 等）
│   └── deps.py          # 依赖注入
├── services/
│   └── generation.py    # 生图编排服务 (GenerationService)
├── dingtalk/
│   └── client.py        # 钉钉 SDK 客户端 (DingTalkClient) — 记录用原始 dict 操作
├── generator/
│   ├── __init__.py     # 包入口，re-export AIGenerator
│   └── engine.py       # AI 生图引擎统一入口 (AIGenerator)
└── models/
    ├── request.py       # API 请求模型
    └── response.py      # API 响应模型
```

## 核心数据流

```
用户点击"生成按钮"
  → 钉钉自动化 → HTTP POST {record_id}
  → FastAPI 接收 → 后台任务处理
  → 钉钉SDK获取记录（素材图、提示词、模型）
  → AI生图引擎生成图片（图生图 img2img）
  → 回写钉钉表格（生成图片、生成结果、生成时间）
```

## 开发规范

### Git 提交规范

使用 Conventional Commits：
```
feat(dingtalk): 实现多维表格记录读写
fix(generator): 修复API超时未重试
docs(api): 补充错误码说明
refactor(services): 生图流程抽为可插拔Pipeline
test(services): 添加端到端生图测试
chore(deps): 更新httpx版本
```

### 代码规范

- **全流程异步**：所有 I/O 操作使用 `async/await`，禁止同步阻塞（`requests`、`time.sleep`、同步文件读写等）
- 钉钉 SDK 统一使用 `*_async` 方法
- HTTP 请求使用 `httpx.AsyncClient`
- 使用 ruff 格式化（line-length=100, 双引号）
- 模块内部定义专用异常类
- 日志用 loguru，关键节点必须打日志

### 配置管理

敏感配置通过环境变量注入（`.env`），非敏感配置放在 `configs/config.toml`，禁止硬编码。

## 文档索引

- `docs/ARCHITECTURE.md` — 系统架构设计
- `docs/API.md` — HTTP 接口文档
- `docs/MODULES.md` — 模块职责与类设计
- `docs/WORKFLOW.md` — 开发环境与工作流
- `docs/ROADMAP.md` — 迭代路线图
- `docs/DEPLOYMENT.md` — 生产部署指南（GitHub Actions + 日志管理）
