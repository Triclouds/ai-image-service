# 模块职责与类设计

## 1. 模块总览

```
src/ai_gen_image/
├── main.py                 # FastAPI 应用入口
├── config.py               # 配置管理
├── api/                    # API 层 → 接收请求、参数校验、路由
│   ├── router.py           #   FastAPI Router
│   └── deps.py             #   依赖注入（配置、钉钉客户端）
├── services/               # 业务编排层 → 生图主流程
│   └── generation.py       #   GenerationService
├── dingtalk/               # 钉钉集成层 → 封装SDK/OpenAPI
│   ├── client.py           #   DingTalkClient
│   └── models.py           #   钉钉数据结构
├── generator/              # AI生图引擎层 → 对接不同模型
│   ├── base.py             #   BaseGenerator（抽象基类）
│   └── engines.py          #   具体引擎实现
└── models/                 # 通用数据模型
    ├── request.py          #   GenerateRequest
    └── response.py         #   TaskResponse
```

---

## 2. `config.py` — 配置管理

使用 `pydantic-settings` 从环境变量加载配置。

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 钉钉配置
    dingtalk_app_key: str
    dingtalk_app_secret: str
    dingtalk_table_id: str = ""        # 默认表格ID
    dingtalk_space_id: str = ""        # 云空间ID（图片上传用）

    # 生图配置
    default_model: str = "flux-1.1-pro"  # 默认生图模型

    # 服务配置
    api_key: str                        # API 鉴权Key
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # 生图API Keys（按模型区分）
    openai_api_key: str = ""
    stability_api_key: str = ""

    class Config:
        env_file = "configs/.env"
```

---

## 3. `api/router.py` — 路由层

单一职责：接收HTTP请求 → 校验 → 调用Service → 返回响应。

```python
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
    background_tasks.add_task(service.process, req.record_id, req.table_id)
    return {
        "status": "accepted",
        "message": "任务已提交",
        "record_id": req.record_id,
        "timestamp": datetime.utcnow().isoformat(),
    }
```

---

## 4. `services/generation.py` — 生图编排服务

核心业务编排：把多个步骤串联起来，处理异常，回写结果。

```python
class GenerationService:
    """生图编排服务。"""

    def __init__(self, dingtalk: DingTalkClient, generator: BaseGenerator):
        self.dingtalk = dingtalk
        self.generator = generator

    async def process(self, record_id: str, table_id: str = None) -> None:
        """
        完整生图流程：
        1. 获取记录数据
        2. 下载素材图
        3. 调用AI生图
        4. 上传生成图片
        5. 回写结果
        """
        table_id = table_id or settings.dingtalk_table_id
        try:
            record = await self.dingtalk.get_record(table_id, record_id)
            ref_image_bytes = await self._download_image(record.get("素材图"))
            result_bytes = await self.generator.generate(
                prompt=record["提示词"],
                model=record.get("生图模型", settings.default_model),
                reference_image=ref_image_bytes,
            )
            file_id = await self.dingtalk.upload_image(result_bytes)
            await self.dingtalk.update_record(table_id, record_id, {
                "生成图片": [file_id],
                "生成结果": "成功",
                "生成时间": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error(f"生图失败 record_id={record_id}: {e}")
            await self.dingtalk.update_record(table_id, record_id, {
                "生成结果": f"失败: {str(e)}",
                "生成时间": datetime.utcnow().isoformat(),
            })
```

---

## 5. `dingtalk/client.py` — 钉钉API客户端

封装钉钉 OpenAPI 调用，管理 Token 缓存。

```python
class DingTalkClient:
    """钉钉 OpenAPI 客户端。"""

    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self.http = httpx.AsyncClient(base_url="https://api.dingtalk.com")

    async def _get_token(self) -> str: ...
    async def get_record(self, table_id: str, record_id: str) -> dict: ...
    async def update_record(self, table_id: str, record_id: str, fields: dict) -> None: ...
    async def upload_image(self, image_bytes: bytes, filename: str) -> str: ...
    async def download_file(self, file_url: str) -> bytes: ...
    async def close(self) -> None: ...
```

关键方法说明：

| 方法 | 功能 | 钉钉API |
|------|------|---------|
| `_get_token` | 获取/刷新 access_token | `/v1.0/oauth2/accessToken` |
| `get_record` | 获取多维表格记录 | `/v1.0/table/{table_id}/records/{record_id}` |
| `update_record` | 更新多维表格记录 | PUT `/v1.0/table/{table_id}/records/{record_id}` |
| `upload_image` | 上传文件到云空间 | `/v1.0/drive/spaces/{space_id}/files` |
| `download_file` | 下载钉钉文件 | 文件下载URL |

---

## 6. `generator/base.py` — 生图引擎抽象

策略模式：不同模型实现统一接口，方便扩展。

```python
from abc import ABC, abstractmethod

class BaseGenerator(ABC):
    """生图引擎抽象基类。"""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        model: str,
        reference_image: bytes | None = None,
        **kwargs,
    ) -> bytes:
        """
        调用AI模型生成图片。

        Args:
            prompt: 提示词。
            model: 模型名称。
            reference_image: 参考图（图生图/风格迁移用）。

        Returns:
            生成的图片字节流。
        """
        ...

    @abstractmethod
    def supported_models(self) -> list[str]: ...
```

### 具体引擎实现

- **OpenAIEngine**: DALL-E 3 / 未来 DALL-E 后续版本
- **FluxEngine**: Flux.1 Pro（通过 Replicate / Together AI 等平台）
- **StableDiffusionEngine**: Stability AI API
- **CustomAPIEngine**: 自部署 ComfyUI / Automatic1111 的 HTTP API

---

## 7. `models/` — 数据模型

```python
# request.py
from pydantic import BaseModel

class GenerateRequest(BaseModel):
    record_id: str
    table_id: str | None = None

# response.py
class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    record_id: str
    result: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
```

---

## 8. 依赖关系图

```
api/router.py ──Depends──▶ services/generation.py
                               │
                     ┌─────────┼─────────┐
                     ▼         ▼         ▼
              dingtalk/    generator/  models/
              client.py    engines.py  request.py
```

所有外部依赖（钉钉SDK、AI API Key）通过 `config.py` → `deps.py` 注入，便于单元测试 mock。
