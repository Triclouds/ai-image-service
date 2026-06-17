# 模块职责与类设计

## 1. 模块总览

```
src/
├── main.py             # FastAPI 应用入口
├── config.py           # 配置管理
├── api/                # API 层 → 接收请求、参数校验、路由
│   ├── router.py       #   FastAPI Router
│   └── deps.py         #   依赖注入（配置、钉钉客户端）
├── services/           # 业务编排层 → 生图主流程
│   └── generation.py   #   GenerationService
├── dingtalk/           # 钉钉集成层 → 封装钉钉 Python SDK
│   └── client.py       #   DingTalkClient（记录用原始 dict 操作）
├── generator/          # AI生图引擎层 → 按 model 分派到 Google / OpenAI SDK
│   ├── __init__.py     #   包入口，re-export AIGenerator
│   └── engine.py       #   AIGenerator（统一入口）
└── models/             # 通用数据模型
    ├── request.py      #   GenerateRequest
    └── response.py     #   TaskResponse
```

---

## 2. `config.py` — 配置管理

使用两阶段加载：`pydantic-settings` 从 `.env` 加载敏感字段 + `tomllib`（Python 3.11+ 内置）从 `config.toml` 加载非敏感字段。

```python
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

# ── 非敏感配置模型（与 config.toml 嵌套结构一一对应）──

class TableConfig(BaseModel):
    """单个 AI 表格配置，对应 [[dingtalk.tables]]。"""
    key: str
    base_id: str
    sheet_id: str
    prompt_field: str = "提示词"
    model_field: str = "生图模型"
    reference_image_field: str = "素材图"
    result_image_field: str = "生成图片"
    result_status_field: str = "生成结果"
    result_time_field: str = "生成时间"

class ModelConfig(BaseModel):
    """AI 模型配置，对应 [ai.model."xxx"]。"""
    endpoint: str
    model_name: str
    provider: str  # "google" 或 "openai"，用于路由到对应 SDK

class ServerConfig(BaseModel):
    """服务配置，对应 [server]。"""
    host: str = "0.0.0.0"
    port: int = 8030
    log_level: str = "INFO"
    max_concurrency: int = 5

class RetryConfig(BaseModel):
    """重试配置，对应 [ai.retry]。"""
    initial_delay: int = 2
    max_retries: int = 1

class AiConfig(BaseModel):
    """AI 中转配置，对应 [ai]。"""
    default_model: str = "Nano Banana 2"
    base_url: str = "https://api.vectorengine.ai"
    retry: RetryConfig = RetryConfig()
    models: dict[str, ModelConfig] = Field(default_factory=dict)

class DingtalkConfig(BaseModel):
    """钉钉表格配置，对应 [dingtalk]。"""
    default_table: str = "clothing"
    tables: List[TableConfig] = []

# ── 敏感配置：仅从 .env / 系统环境变量加载 ──

class _EnvSettings(BaseSettings):
    """敏感配置，仅从 .env 加载。不写入 config.toml。"""
    dingtalk_app_key: str
    dingtalk_app_secret: str
    dingtalk_operator_id: str
    api_key: str
    zhuozhi_nanobanana_api_key: str = ""
    zhuozhi_gpt_image_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

# ── 全局配置：由 .env（敏感）+ config.toml（非敏感）组合 ──

class Settings(BaseSettings):
    """全局配置。

    加载顺序：
    1. pydantic-settings 从 .env / 系统环境变量加载敏感字段
    2. Python 3.11+ 内置 tomllib 加载 config.toml 非敏感字段
    3. 系统环境变量覆盖 config.toml 同名字段（最高优先级）
    """

    # 敏感字段（仅 .env / 系统环境变量）
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_operator_id: str = ""
    api_key: str = ""
    zhuozhi_nanobanana_api_key: str = ""
    zhuozhi_gpt_image_api_key: str = ""

    # 非敏感字段（由 __init__ 从 config.toml 填充）
    server: ServerConfig = ServerConfig()
    ai: AiConfig = AiConfig()
    dingtalk: DingtalkConfig = DingtalkConfig()

    def __init__(self, **kwargs):
        # 先保留用户传入的显式参数（如测试时注入 mock）
        explicit = dict(kwargs)
        super().__init__(**explicit)

        # 从 config.toml 加载非敏感配置
        self._load_toml_config(explicit)

    def _load_toml_config(self, explicit: dict) -> None:
        """使用 tomllib 加载 config.toml，除非键已被显式参数覆盖。"""
        toml_path = os.environ.get("CONFIG_PATH", "configs/config.toml")
        toml_file = Path(toml_path)
        if not toml_file.exists():
            return

        with toml_file.open("rb") as f:
            data = tomllib.load(f)

        # 逐段加载，只更新不在 explicit 中的字段
        if "server" in data and "server" not in explicit:
            merged = self.server.model_dump() | data["server"]
            self.server = ServerConfig(**merged)
        if "dingtalk" in data and "dingtalk" not in explicit:
            merged = self.dingtalk.model_dump() | data["dingtalk"]
            self.dingtalk = DingtalkConfig(**merged)
        if "ai" in data and "ai" not in explicit:
            ai_data = data["ai"]
            if "models" in ai_data and "models" not in explicit.get("ai", {}):
                ai_data["models"] = {
                    k: ModelConfig(**v) for k, v in ai_data.pop("models", {}).items()
                }
            merged = self.ai.model_dump() | ai_data
            self.ai = AiConfig(**merged)

        # 系统环境变量覆盖 config.toml 的非敏感字段
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """系统环境变量覆盖非敏感配置。"""
        overrides = {
            "SERVER_HOST": ("server", "host"),
            "SERVER_PORT": ("server", "port"),
            "LOG_LEVEL": ("server", "log_level"),
            "MAX_CONCURRENCY": ("server", "max_concurrency"),
            "AI_BASE_URL": ("ai", "base_url"),
            "AI_DEFAULT_MODEL": ("ai", "default_model"),
            "AI_RETRY_INITIAL_DELAY": ("ai", "retry", "initial_delay"),
            "AI_RETRY_MAX_RETRIES": ("ai", "retry", "max_retries"),
            "DINGTALK_DEFAULT_TABLE": ("dingtalk", "default_table"),
        }
        for env_key, path in overrides.items():
            if env_key not in os.environ:
                continue
            raw = os.environ[env_key]
            obj = getattr(self, path[0])
            for attr in path[1:-1]:
                obj = getattr(obj, attr)
            # 保持类型
            current = getattr(obj, path[-1])
            if isinstance(current, bool):
                typed = raw.lower() in ("true", "1", "yes")
            elif isinstance(current, int):
                typed = int(raw)
            else:
                typed = type(current)(raw)
            setattr(obj, path[-1], typed)

    # ── 便捷方法 ──

    def get_table(self, table_key: str | None = None) -> TableConfig:
        key = table_key or self.dingtalk.default_table
        for table in self.dingtalk.tables:
            if table.key == key:
                return table
        raise ValueError(f"Table config not found: {key}")

    def get_model(self, model_key: str) -> ModelConfig:
        model = self.ai.models.get(model_key)
        if not model:
            raise ValueError(f"Model config not found: {model_key}")
        return model
```

**配置来源与优先级**（高 → 低）：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 (最高) | 系统环境变量 | `export AI_BASE_URL=...` 覆盖所有下级来源 |
| 2 | `.env` | pydantic-settings 加载，覆盖 config.toml 同名字段 |
| 3 | `configs/config.toml` | tomllib 加载非敏感配置（字段映射、endpoint 等） |
| 4 (最低) | 代码默认值 | `host = "0.0.0.0"` 等 class 内联默认值 |

> **核心设计**：`.env` 只放真正的敏感信息（Key/Secret），`config.toml` 放字段映射、endpoint、超时等非敏感配置。部署时如需覆盖 config.toml 中的非敏感值，用系统环境变量即可，无需改配置文件。

**多表格设计原则**：
- 代码中不硬编码任何字段名，全部从 `TableConfig` 读取
- 新增表格只需在 `config.toml` 中加一段 `[[dingtalk.tables]]`，不用改代码
- `operator_id` 全局一个，不区分表格

---

## 3. `api/router.py` — 路由层

单一职责：接收HTTP请求 → 校验 → 调用Service → 返回响应。

```python
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends

router = APIRouter()

@router.post("/api/v1/generate", status_code=202)
async def trigger_generation(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    service: GenerationService = Depends(get_generation_service),
    api_key: str = Depends(verify_api_key),
):
    """接收钉钉自动化回调，触发异步生图流程。"""
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return {
        "status": "accepted",
        "message": "任务已提交",
        "record_id": req.record_id,
        "timestamp": datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }
```

---

## 4. `services/generation.py` — 生图编排服务

核心业务编排：把多个步骤串联起来，处理异常，回写结果。

```python
class GenerationService:
    """生图编排服务。"""

    def __init__(self, dingtalk: DingTalkClient, generator: AIGenerator, settings: Settings):
        self.dingtalk = dingtalk
        self.generator = generator
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.server.max_concurrency)

    async def process(self, record_id: str, table_key: str | None = None) -> None:
        """
        完整生图流程：
        1. 获取表格配置（根据 table_key）
        2. 获取记录数据
        3. 校验：提示词必填、素材图必填
        4. 下载素材图（图生图输入）
        5. 调用AI生图
        6. 上传结果图片到钉钉云空间
        7. 回写结果到钉钉表格

        本方法不做重试。各自网络调用方法（DingTalkClient、AIGenerator 内部）已通过
        @retry_on_network_error 独立处理重试，详见"重试设计"章节。

        并发控制：通过 Semaphore 限制最大并发生图数，超出时排队等待（无超时）。
        错误处理：业务错误统一回写表格"失败: {str(e)}"，完整 traceback 仅写日志。
        字段映射：所有字段名从 TableConfig 读取，不硬编码。
        """
        async with self._semaphore:
            try:
                table_config = self.settings.get_table(table_key)
                record = await self.dingtalk.get_record(table_config, record_id)
                prompt = record["fields"].get(table_config.prompt_field)
                if not prompt:
                    await self._update_failure(table_config, record_id, "提示词不能为空")
                    return
                ref_image_data = record["fields"].get(table_config.reference_image_field)
                if not ref_image_data:
                    await self._update_failure(table_config, record_id, "素材图不能为空")
                    return
                ref_image_bytes = await self.dingtalk.download_file(ref_image_data[0]["url"])
                model = record["fields"].get(table_config.model_field, self.settings.ai.default_model)
                result_bytes = await self.generator.generate(
                    model=model,
                    prompt=prompt,
                    reference_image=ref_image_bytes,
                )
                attachment_info = await self.dingtalk.upload_attachment(
                    table_config, result_bytes, f"generated_{record_id}.png"
                )
                await self.dingtalk.update_record(table_config, record_id, {
                    table_config.result_image_field: [attachment_info],
                    table_config.result_status_field: "成功",
                    table_config.result_time_field: datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
            except Exception as e:
                logger.exception(f"生图失败 record_id={record_id}")
                await self._update_failure(table_config, record_id, str(e))
```

---

## 5. `dingtalk/client.py` — 钉钉API客户端

封装钉钉 Python SDK 调用，管理 Token 缓存。

```python
from alibabacloud_dingtalk.notable_1_0 import Client as NotableClient
from alibabacloud_tea_openapi import models as open_api_models

class DingTalkClient:
    """钉钉 OpenAPI 客户端。
    
    初始化只绑定全局共享参数（app_key、app_secret、operator_id）。
    app_key/app_secret 仅用于自行获取 access_token（通过 httpx 调 OAuth2），
    不传入 SDK Client。SDK Client 的 token 通过 header 传入。
    base_id 和 sheet_id 在方法级别通过 TableConfig 传入，支持多表格场景。
    """

    def __init__(self, app_key: str, app_secret: str, operator_id: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.operator_id = operator_id
        # SDK Client 初始化：官方 SDK 使用 Config()，不传 app_key/app_secret
        config = open_api_models.Config()
        config.protocol = "https"
        config.region_id = "central"
        self._client = NotableClient(config)

    async def get_record(self, table_config: TableConfig, record_id: str) -> dict: ...
    async def update_record(self, table_config: TableConfig, record_id: str, fields: dict) -> None: ...
    async def upload_attachment(self, table_config: TableConfig, image_bytes: bytes, filename: str) -> dict: ...
    async def download_file(self, file_url: str) -> bytes: ...
```

**说明**：
- `base_id` 和 `sheet_id` 从 `TableConfig` 读取，每次 API 调用时传入
- `operator_id` 从 `.env` 读取，全局共享
- 素材图下载：通过 `httpx.AsyncClient.get()` + access token header 下载（参考 `docs/sdk_docs/SDK实现指南.md` 第6节）
- 附件上传：3步流程（获取上传信息 → PUT上传 → 返回 attachment 格式数据），参考 `docs/sdk_docs/上传附件.md`
- Token 管理：缓存 + 自动刷新，过期前 5 分钟主动刷新

---

## 6. `generator/` — AI 生图引擎

统一入口，按 model 分派到对应 SDK Client。

```python
import base64
import io
from PIL import Image
from google import genai
from openai import AsyncOpenAI


def _to_png_bytes(image_bytes: bytes) -> bytes:
    """将任意格式图片字节统一转为 PNG，确保后续上传 mediaType 正确。"""
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class AIGenerator:
    """AI 生图引擎，统一调度 Google / OpenAI SDK。

    路由逻辑：根据 config.toml 中 model 配置的 provider 字段
    （"google" / "openai"）分派到对应 SDK。新增模型只需在 config.toml
    中添加 [ai.model."xxx"] 并填入正确 provider，无需改代码。

    模型名 → 真实 model_name 的映射也从 config.toml 读取：
    钉钉表格"生图模型"字段值 → Settings.get_model() → 真实 model_name。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(
        self,
        model: str,
        prompt: str,
        reference_image: bytes | None = None,
        table_config: TableConfig | None = None,
    ) -> bytes:
        """根据 model 参数（钉钉表格值）分派到对应 SDK，返回统一 PNG 格式字节流。

        模型名通过 Settings.get_model(model).model_name 获取真实 SDK 模型名。
        provider 字段决定路由到哪个 SDK。
        """
        model_cfg = self.settings.get_model(model)
        if model_cfg.provider == "google":
            raw = await self._generate_nano(model_cfg.model_name, prompt, reference_image)
        elif model_cfg.provider == "openai":
            raw = await self._generate_gpt(model_cfg.model_name, prompt, reference_image)
        else:
            raise ValueError(f"Unsupported provider: {model_cfg.provider} (model={model})")
        return _to_png_bytes(raw)

    async def _generate_nano(self, model_name: str, prompt: str, image_bytes: bytes) -> bytes:
        """调用 Google genai SDK 图生图（img2img）。

        官方示例：contents=[prompt, PIL_Image]，响应通过 part.inline_data.as_image() 取。
        这里将 bytes 转为 PIL Image 后传入，再将输出 PIL Image 转回 bytes。
        """
        pil_image = Image.open(io.BytesIO(image_bytes))
        response = await self.nano_client.models.generate_content(
            model=model_name,
            contents=[prompt, pil_image],
        )
        for part in response.parts:
            if part.inline_data is not None:
                pil_image = part.as_image()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                return buf.getvalue()
        raise ValueError("No image generated in response")

    async def _generate_gpt(self, model_name: str, prompt: str, image: bytes) -> bytes:
        response = await self.gpt_client.images.edit(
            model=model_name,
            image=image,
            prompt=prompt,
            n=1,
            response_format="b64_json",
        )
        # response.data[0].b64_json 是 base64 编码的 PNG 数据
        return base64.b64decode(response.data[0].b64_json)
```

**模型路由**：

| 钉钉表格值 | SDK | 真实 model_name | 中转站 endpoint |
|------------|-----|-----------------|-----------------|
| `Nano Banana Pro` | Google genai SDK | `gemini-3-pro-image-preview` | `/v1beta/models/...:generateContent` |
| `Nano Banana 2` | Google genai SDK | `gemini-3.1-flash-image-preview` | `/v1beta/models/...:generateContent` |
| `GPT Image 2` | OpenAI SDK | `gpt-image-2` | `/v1/images/edits` |

以上映射全部从 `config.toml [ai.model.*]` 读取，代码不硬编码。所有模型共用 `https://api.vectorengine.ai` 中转站。

---

## 7. `models/` — 数据模型

本项目不使用 Pydantic model 封装钉钉记录数据，直接用 dict 操作。

```python
# request.py
from pydantic import BaseModel
from typing import Optional

class GenerateRequest(BaseModel):
    record_id: str
    table_key: Optional[str] = None  # 可选，默认使用 config.toml 中的 default_table
```

**钉钉记录数据结构**（SDK 返回的原始 dict）：
```python
# get_record 返回
{
    "id": "ePoxxxx",
    "fields": {
        "款号": "ABC123",
        "素材图": [{"filename": "test.jpg", "size": 12345, "type": "jpg", "url": "/core/api/resources/img/xxx"}],
        "提示词": "a cute cat",
        "生图模型": "Nano Banana Pro"
    },
    "createdBy": {"unionId": "xxx"},
    "createdTime": 1752482830554,
}
```

**附件字段格式**：
- **读取时**：`[{"filename": "...", "size": N, "type": "...", "url": "下载链接"}]`
- **写入时**：`[{"filename": "...", "size": N, "type": "MIME类型", "url": "resourceUrl", "resourceId": "xxx"}]`
- 注意：读取时没有 `resourceId`，写入时必须包含

---

## 8. `api/deps.py` — 依赖注入

FastAPI 的依赖注入模块，统一管理所有共享对象的生命周期。

```python
from functools import lru_cache
from config import Settings
from dingtalk.client import DingTalkClient
from generator import AIGenerator
from services.generation import GenerationService

@lru_cache
def get_settings() -> Settings:
    """Settings 只加载一次，全局共享。"""
    return Settings()

@lru_cache
def get_dingtalk_client() -> DingTalkClient:
    """Client 只创建一次，Token 缓存一直有效。"""
    settings = get_settings()
    return DingTalkClient(
        app_key=settings.dingtalk_app_key,
        app_secret=settings.dingtalk_app_secret,
        operator_id=settings.dingtalk_operator_id,
    )

@lru_cache
def get_ai_generator() -> AIGenerator:
    """AI 引擎只创建一次，SDK Client 复用。"""
    settings = get_settings()
    return AIGenerator(settings)

@lru_cache
def get_generation_service() -> GenerationService:
    """组合以上所有依赖。"""
    return GenerationService(
        dingtalk=get_dingtalk_client(),
        generator=get_ai_generator(),
        settings=get_settings(),
    )
```

**设计原则**：
- 全部使用 `@lru_cache` 实现 singleton，应用启动时初始化一次
- 钉钉 Client 的 Token 缓存在整个应用生命周期内有效
- 测试时可通过 `app.dependency_overrides` 轻松替换 mock 对象

## 9. 依赖关系图

```
api/router.py ──Depends──▶ services/generation.py
                                 │
                       ┌─────────┼─────────┐
                       ▼         ▼         ▼
                dingtalk/    generator/  models/
                client.py    __init__.py  request.py
                      ▲            ▲
                      │            │
                api/deps.py ◀──────┘
                      │
                config.py (Settings)
```

所有外部依赖（钉钉SDK、AI API Key）通过 `config.py` → `deps.py` 注入，便于单元测试 mock。

## 10. 重试设计

### 10.1 原则

- **只看异常类型，不看所在步骤**：只要是网络层面的异常（ConnectError、Timeout、5xx），不管发生在哪一步都重试
- **业务异常不重试**：4xx、参数校验、权限不足、记录不存在等，重试也没用
- **不在 process() 层面做整体重试**：各自网络调用方法内部独立重试，避免重复获取记录/下载素材

### 10.2 统一重试装饰器

定义一个可复用的装饰器，统一控制重试策略：

```python
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import httpx

# 所有网络调用统一的重试策略
# 对应 config.toml [ai.retry]：max_retries = 1（重试 1 次）
# stop_after_attempt(2) = 首次 + 1 次重试 = 总 2 次
retry_on_network_error = retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.NetworkError,
    )),
)
```

### 10.3 应用范围

| 方法 | 是否重试 | 说明 |
|------|----------|------|
| `DingTalkClient.get_record()` | ✅ | 网络异常重试 |
| `DingTalkClient.download_file()` | ✅ | 网络异常重试 |
| `DingTalkClient.upload_attachment()` | ✅ | 网络异常重试 |
| `DingTalkClient.update_record()` | ✅ | 网络异常重试 |
| `AIGenerator.generate()` | ✅ | 网络异常重试 |
| `GenerationService.process()` | ❌ | 不重试，各子方法已自行处理 |

```python
# dingtalk/client.py
class DingTalkClient:
    @retry_on_network_error
    async def get_record(self, ...): ...

    @retry_on_network_error
    async def download_file(self, ...): ...

    @retry_on_network_error
    async def upload_attachment(self, ...): ...

    @retry_on_network_error
    async def update_record(self, ...): ...
```

```python
# generator/engine.py
class AIGenerator:
    @retry_on_network_error
    async def generate(self, ...): ...
```

### 10.4 配置

通过 `config.toml` 和 `.env` 统一控制：

```toml
[ai.retry]
initial_delay = 2   # 重试间隔（秒）
max_retries = 1     # 重试次数（不含首次）
```

```python
# config.py
# 通过 settings.ai.retry 读取：
#   settings.ai.retry.max_retries    # → 1
#   settings.ai.retry.initial_delay  # → 2
```

## 11. 异步规范

**全流程异步，禁止同步阻塞。**

| 操作 | 正确方式 | 禁止方式 |
|------|----------|----------|
| 钉钉 SDK | `client.get_record_with_options_async()` | `client.get_record_with_options()` |
| HTTP 请求 | `httpx.AsyncClient()` | `requests.get()` |
| 文件读写 | `aiofiles.open()` | `open()` |
| 延迟等待 | `await asyncio.sleep()` | `time.sleep()` |
| 并发控制 | `asyncio.Semaphore` | `threading.Lock` |
| 后台任务 | `BackgroundTasks.add_task()` | `threading.Thread` |

---

## 9. 视频生成模块

视频模块**完全独立于图片模块**，不复用 AIGenerator 的 google/openai SDK 路由，新增独立文件与依赖。

### 9.1 新增模块

| 文件 | 职责 |
|------|------|
| `src/generator/video_engine.py` | `VideoGenerator`：按 model_name 前缀路由到三家厂商（Kling / Hailuo / Wanxiang），统一提交 + 轮询 + 下载视频字节 |
| `src/services/video_generation.py` | `VideoGenerationService`：9 步编排（与 GenerationService 7 步骨架对称，步骤 5-7 在 VideoGenerator 内部） |
| `src/models/video_request.py` | `VideoGenerateRequest`（字段同 GenerateRequest，但 `table_key` 必填） |

### 9.2 `VideoGenerator` 类（`src/generator/video_engine.py`）

```python
class VideoGenerator:
    """视频生成统一引擎：提交任务 + 轮询 + 下载结果。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(
        self,
        model: str,                       # 钉钉表格原值，如 "kling-v2-5-turbo"
        prompt: str,
        reference_image: bytes,
        table_config: VideoTableConfig,
    ) -> bytes:
        """提交 → 轮询 → 下载视频字节。"""
        provider = _resolve_provider(model)             # 前缀路由
        provider_cfg = self.settings.get_video_provider(provider)
        api_key = self.settings.get_api_key(table_config.video_api_key_env)
        image_b64 = base64.b64encode(_to_png_bytes(reference_image)).decode("ascii")
        # ... 各家 _submit_x + _poll_x 实现
        return await self._download_video(video_url)
```

**关键设计**：
- `model` 参数即钉钉单元格原值，直接作为请求体的 `model_name` / `model` 字段发出，**服务侧零翻译**
- 首帧图统一转 PNG 后 base64 编码，三家中转站均支持
- 私有 `_retry_on_network_error` 模式（与 AIGenerator / DingTalkClient 同模式，独立维护避免耦合）
- `_poll_until_done` 统一三家轮询骨架：初始等待 → 间隔轮询 → 总超时

**`_resolve_provider` 前缀路由**：

```python
def _resolve_provider(model_name: str) -> str:
    name = model_name.strip().lower()
    if name.startswith("kling"):
        return "kling"
    if name.startswith(("minimax-hailuo", "minimax", "hailuo")):
        return "hailuo"
    if name.startswith(("happyhorse", "wanx", "wan")):
        return "wanxiang"
    raise ValueError(f"Unknown video model (cannot route to provider): {model_name}")
```

**终态归一**：三家状态字符串大小写各异（`succeed` / `Success` / `SUCCEEDED`），`_poll_until_done` 内部用 `status.lower() in _TERMINAL_SUCCESS` 统一判定。

### 9.3 `VideoGenerationService` 类（`src/services/video_generation.py`）

9 步编排：

| 步骤 | 任务 |
|------|------|
| 1 | `settings.get_video_table(table_key)` |
| 2 | `dingtalk.get_record()` — 校验提示词/视频模型/首帧图三必填 |
| 3 | 同上 |
| 4 | `dingtalk.download_file()` — 下载首帧图 |
| 5-7 | `video_generator.generate()` — 内部完成 base64 编码、提交、轮询、下载 mp4 |
| 8 | `dingtalk.upload_attachment(media_type="video/mp4")` |
| 9 | `dingtalk.update_record()` — 回写"生成视频"附件 + "成功" + "生成时间" |

并发控制：`Semaphore(settings.server.video_max_concurrency=3)`，与图片 `Semaphore(max_concurrency=5)` 完全独立。

### 9.4 `VideoGenerateRequest`（`src/models/video_request.py`）

```python
class VideoGenerateRequest(BaseModel):
    record_id: str
    table_key: str  # 必填（视频表无 default 兜底）

    @field_validator("record_id") ... # 不为空
    @field_validator("table_key") ... # 不为空
```

### 9.5 配置类扩展（`src/config.py`）

| BaseModel | 对应 toml 节 | 说明 |
|-----------|--------------|------|
| `VideoProviderConfig` | `[ai.video_provider."xxx"]` | 仅含 `base_url`，model_name 由钉钉表格透传 |
| `VideoTableConfig` | `[[dingtalk.video_tables]]` | 字段映射（`prompt_field`/`video_model_field`/`reference_image_field`/`result_video_field`/...） |
| `VideoPollConfig` | `[ai.video.poll]` | `initial_wait=10`/`interval=5`/`max_total=600` |

`Settings` 新增 `ai.video_providers` / `dingtalk.video_tables` / `ai.video_poll` 字段，新增方法：
- `get_video_table(table_key)` — 不复用 `default_table`，找不到抛 `ConfigError`
- `get_video_provider(provider_key)` — 不存在抛 `ConfigError`

环境变量覆盖键：`VIDEO_MAX_CONCURRENCY`、`AI_VIDEO_POLL_INITIAL_WAIT` / `_INTERVAL` / `_MAX_TOTAL`。

### 9.6 依赖注入（`src/api/deps.py`）

新增两个 `@lru_cache` 单例（与图片侧对称）：

```python
@lru_cache
def get_video_generator() -> VideoGenerator: ...

@lru_cache
def get_video_generation_service() -> VideoGenerationService:
    return VideoGenerationService(
        dingtalk=get_dingtalk_client(),        # 复用：共享 access_token 缓存
        video_generator=get_video_generator(),
        settings=get_settings(),
    )
```

### 9.7 API 路由（`src/api/router.py`）

```python
@router.post("/api/v1/video/generate", response_model=TaskResponse, status_code=200)
async def trigger_video_generation(
    req: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    service: VideoGenerationService = Depends(get_video_generation_service),
    _api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return TaskResponse(status="accepted", message="视频生成任务已提交...", ...)
```

### 9.8 钉钉客户端扩展（`src/dingtalk/client.py`）

`upload_attachment` 加 `media_type` 入参（默认 `"image/png"`，向后兼容）：

```python
async def upload_attachment(
    self,
    table_config: TableConfig,
    file_bytes: bytes,
    filename: str,
    media_type: str = "image/png",  # 视频场景传 "video/mp4"
) -> dict: ...
```

参数名 `image_bytes` → `file_bytes`，**所有现有调用方使用位置参数**，向后兼容。