# 视频生成功能接入计划（Kling / 海螺 / 通义万象）

## Context

当前服务（`ai-image-service`）支持钉钉多维表格驱动的 AI **图片**生成，三家品牌各一张表（zhuozhi/huapu/ahmi-base），通过 `POST /api/v1/generate` 触发，调用 `Nano Banana` / `GPT Image` 等图像模型。

业务希望在不影响现有图片生成的前提下，**新增完全独立的视频生成能力** — 独立接口、独立编排、独立 generator、独立配置，仅复用钉钉客户端与基础设施（配置加载、日志、异常、依赖注入框架）。沿用钉钉表格触发 → 服务回写的同一套交互模式。首批接入三家：

- **快手可灵 (Kling)** — `POST /kling/v1/videos/image2video`
- **海螺视频 (Hailuo)** — `POST /minimax/v1/video_generation`
- **通义万象 (Wanxiang)** — `POST /alibailian/api/v1/services/aigc/video-generation/video-synthesis`

三家 API 均为 **异步任务模式（提交 → 轮询 → 取结果 URL）**，与现有图片同步生成（一次调用直接返回字节）的模型差异极大。视频生成耗时通常 30s–5min，是本次接入的核心架构挑战。

**用户已确认的关键决策**：
- 视频表与图片表 **独立**（新增 zhuozhi-video / huapu-video / ahmi-video）
- 视频接口与图片接口 **物理隔离**（新增 `POST /api/v1/video/generate`）
- 视频模型与图片模型 **完全独立**，不复用 Nano Banana / GPT Image 的 google/openai provider 路由
- **模型 ID 由钉钉表格"视频模型"字段直接透传**到第三方 API 的 `model_name` 字段，不在服务侧维护"显示名 → API model_name"映射；钉钉表格填什么（如 `kling-v2-5-turbo` / `MiniMax-Hailuo-2.3` / `happyhorse-1.0-i2v`），就以这个原值调 API
- 首批仅做 **图生视频 (image-to-video)**

## 三家 API 速查表（来自 vectorengine.apifox.cn）

所有请求统一通过 `https://api.vectorengine.ai` 中转，鉴权 `Authorization: Bearer <TOKEN>`。

| 厂商 | 提交 endpoint | 查询 endpoint | 提交响应 task_id 路径 | 查询响应 状态字段 | 视频 URL 路径 |
|------|---------------|---------------|-----------------------|-------------------|---------------|
| Kling | `POST /kling/v1/videos/image2video` | `GET /kling/v1/videos/image2video/{task_id}` | `data.task_id` | `data.task_status` (`submitted`/`processing`/`succeed`/`failed`) | `data.task_result.videos[0].url` |
| Hailuo | `POST /minimax/v1/video_generation` | `GET /minimax/v1/query/video_generation?task_id={id}` | `task_id` | `data.status` (`Queueing`/`Processing`/`Success`/`Fail`) | `data.data.file.download_url` |
| Wanxiang | `POST /alibailian/api/v1/services/aigc/video-generation/video-synthesis` | `GET /alibailian/api/v1/tasks/{task_id}` | `output.task_id` | `output.task_status` (`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`) | `output.video_url` |

各家请求体核心字段（图生视频）：

```jsonc
// Kling — 接受 URL 或 base64
{
  "model_name": "kling-v2-5-turbo",          // kling-v1 / kling-v1-5 / kling-v1-6 / kling-v2-5-turbo
  "image": "<URL 或 base64>",                 // jpg/png ≤10MB ≥300x300
  "prompt": "...", "negative_prompt": "",
  "duration": "5", "aspect_ratio": "16:9", "cfg_scale": 0.5, "mode": "std"
}
// Hailuo
{
  "model": "MiniMax-Hailuo-2.3",
  "prompt": "...", "duration": 10,
  "first_frame_image": "<URL 或 base64 data URI>",
  "resolution": "768P", "prompt_optimizer": true
}
// Wanxiang
{
  "model": "happyhorse-1.0-i2v",
  "input": {
    "prompt": "...",
    "media": [{"type": "first_frame", "url": "<URL>"}]
  },
  "parameters": {"resolution": "720P", "duration": 5}
}
```

**素材图传递策略**：钉钉素材图 URL 需 token 访问，无法直接交给第三方 API。统一方案：服务下载素材图 → 转为 `data:image/png;base64,...` 形式作为 `image` / `first_frame_image` / `media[0].url`。三家中转站均支持 base64（Kling 文档明确支持；Hailuo/Wanxiang 若实测不支持，再走"先回写到钉钉云空间获得公网 URL"作为兜底，但当前不预留）。

## 架构方案

复用现有图片生成的"7 步编排骨架"，把"同步生图（一次出字节）"替换为"提交 + 轮询（拿到 mp4 URL）"。新视频流程是 **9 步**：

```
[1] 获取表格配置（按 table_key 命中视频表）
[2] 获取钉钉记录（取 提示词 / 视频模型 / 素材图）
[3] 校验提示词、素材图必填
[4] 下载素材图字节（dingtalk.download_file）
[5] 编码 base64 data URI
[6] 提交视频生成任务（按 provider 路由到 Kling/Hailuo/Wanxiang）→ task_id
[7] 轮询任务状态直至成功 / 失败 / 超时
[8] 从结果 URL 下载 mp4 字节，上传到钉钉云空间（附件，media_type=video/mp4）
[9] 回写表格：生成视频 / 生成结果 / 生成时间
```

并发控制、错误回写、状态推送均沿用 `GenerationService` 已有机制。

## 复用 vs 新增 — 一览

**直接复用，无需改动**：
- `src/main.py` — FastAPI 应用入口、中间件、异常处理
- `src/utils/{logging,middleware,exceptions}.py` — 日志、追踪、异常
- `src/config.py` 三阶段加载机制（`.env` + `config.toml` + 系统环境变量）
- `src/api/router.py` 的 `verify_api_key` 鉴权依赖
- `src/dingtalk/client.py` 的 `_get_access_token` / `get_record` / `update_record` / `download_file` / `_retry_on_network_error`

**复用 + 小幅扩展**：
- `src/dingtalk/client.py::upload_attachment` — 当前 `media_type = "image/png"` 硬编码（`src/dingtalk/client.py:156`）。改为入参 `media_type: str = "image/png"`，并把 `_do_upload` 用到 `media_type` 的两处都改成入参。视频场景调用时传 `"video/mp4"`。
- `src/api/deps.py` — 加 `get_video_generator()` / `get_video_generation_service()` 两个 `@lru_cache` 单例（与 `get_ai_generator()` / `get_generation_service()` 对称）。
- `_retry_on_network_error` — 与 `DingTalkClient._retry_on_network_error`（`src/dingtalk/client.py:92`）/ `AIGenerator._retry_on_network_error`（`src/generator/engine.py:37`）同模式，本地复制一份

**新增模块**：

| 文件 | 职责 |
|------|------|
| `src/config.py`（同文件新增类） | `VideoProviderConfig`、`VideoTableConfig`、`VideoPollConfig`，并在 `Settings` 上加 `video_providers` / `video_tables` / `video_poll` 字段、`get_video_table()` / `get_video_provider()` 方法 |
| `src/generator/video_engine.py` | `VideoGenerator` 类，按 `provider` 路由到三个 `_submit_*` + 统一 `_poll_*` 方法；接口：`async def generate(model, prompt, reference_image, table_config) -> bytes`（内部完成提交 + 轮询 + 下载 mp4） |
| `src/services/video_generation.py` | `VideoGenerationService` 类，9 步流水线编排，参照 `GenerationService.process()` 写法 |
| `src/models/video_request.py` | `VideoGenerateRequest`（字段同 `GenerateRequest`，类型独立便于演进） |
| `src/api/router.py`（同文件新增路由） | `POST /api/v1/video/generate` 和 `GET /api/v1/video/health` |
| `configs/config.toml`（追加节） | 视频模型 + 三张视频表配置（详见下文） |
| `.env` / `configs/.env.example` | 新增 `ZHUOZHI_VIDEO_API_KEY` / `HUAPU_VIDEO_API_KEY` / `AHMI_VIDEO_API_KEY` |
| `tests/test_generator/test_video_engine.py` | 模拟 httpx 响应测试三家 submit + poll |
| `tests/test_services/test_video_generation.py` | mock dingtalk + video_generator，校验 9 步流程 |

## 核心模块设计

### 1. `VideoGenerator`（`src/generator/video_engine.py`）

完全独立于 `AIGenerator`（不复用 google/openai SDK 路由），按从 model_name 解析出的 `provider` 分派：

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
        image_b64 = base64.b64encode(_to_png_bytes(reference_image)).decode()

        if provider == "kling":
            task_id = await self._submit_kling(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_kling(provider_cfg, task_id, api_key)
        elif provider == "hailuo":
            task_id = await self._submit_hailuo(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_hailuo(provider_cfg, task_id, api_key)
        elif provider == "wanxiang":
            task_id = await self._submit_wanxiang(provider_cfg, model, prompt, image_b64, api_key)
            video_url = await self._poll_wanxiang(provider_cfg, task_id, api_key)
        else:
            raise ValueError(f"Unsupported video provider: {provider}")

        return await self._download_video(video_url)
```

**注意**：`model` 参数即钉钉单元格原值，直接作为请求体的 `model_name` / `model` 字段发出，**服务侧零翻译**。

**轮询策略**（统一）：
- 初始等待 10s
- 此后每 5s 轮询一次
- 总超时 10 分钟（可配置 `[ai.video.poll]` 节）
- 终态：`succeed/Success/SUCCEEDED` → 取 URL；`failed/Fail/FAILED` → 抛错
- 提交、轮询、下载视频都走 `_retry_on_network_error`（复用模式，可抽到 `src/utils/retry.py` 单独工具）

**三家 provider 实现要点**（细节由 vectorengine 文档驱动，路径已在速查表给出）：
- `_submit_kling` → POST + JSON body，提取 `data.task_id`
- `_poll_kling` → GET `/kling/v1/videos/image2video/{task_id}`，状态 `succeed` 时返回 `data.task_result.videos[0].url`
- `_submit_hailuo` → POST，提取顶层 `task_id`
- `_poll_hailuo` → GET `?task_id=xxx`，状态 `Success` 时返回 `data.data.file.download_url`
- `_submit_wanxiang` → POST，提取 `output.task_id`
- `_poll_wanxiang` → GET `/tasks/{task_id}`，状态 `SUCCEEDED` 时返回 `output.video_url`

### 2. `VideoGenerationService`（`src/services/video_generation.py`）

`process()` 完全复刻 `GenerationService.process()` 的 7 步骨架（参考 `src/services/generation.py:61-186`），改动点：
- 替换 `generator.generate()` 为 `video_generator.generate()`（内部已完成提交+轮询）
- 改写 step 6/7：`upload_attachment(..., media_type="video/mp4")`，文件名 `generated_{record_id}.mp4`
- 字段写入：`table_config.result_video_field`（替代 `result_image_field`）
- 失败回写复用 `_update_failure`（与 `GenerationService._update_failure` 同模式，本地复制）

### 3. 配置扩展（`src/config.py` + `configs/config.toml`）

**核心原则**：钉钉表格"视频模型"字段填的就是第三方 API 的 `model_name`（原始字符串），服务侧仅需把"前缀/字典"映射到 `provider`，不维护任何"显示名"翻译层。

`src/config.py` 新增类（与现有 `TableConfig` 风格一致；**注意不引入"显示名 → API model_name"映射**）：

```python
class VideoProviderConfig(BaseModel):
    """单个视频厂商配置，对应 [ai.video_provider."xxx"]。"""
    base_url: str
    # provider 标识在 key 上（如 "kling" / "hailuo" / "wanxiang"）

class VideoTableConfig(BaseModel):
    key: str
    base_id: str
    sheet_id: str
    video_api_key_env: str
    prompt_field: str = "提示词"
    video_model_field: str = "视频模型"       # 钉钉单元格直接填 model_name 原值
    reference_image_field: str = "首帧图"
    result_video_field: str = "生成视频"
    result_status_field: str = "生成结果"
    result_time_field: str = "生成时间"

class VideoPollConfig(BaseModel):
    initial_wait: int = 10
    interval: int = 5
    max_total: int = 600  # 10 分钟
```

**Provider 路由**通过 model_name 前缀匹配（在 `VideoGenerator` 内部，无需配置驱动）：

```python
def _resolve_provider(model_name: str) -> str:
    if model_name.startswith("kling"):
        return "kling"
    if model_name.startswith(("MiniMax-Hailuo", "MiniMax", "Hailuo")):
        return "hailuo"
    if model_name.startswith(("happyhorse", "wanx", "wan")):
        return "wanxiang"
    raise ValueError(f"Unknown video model: {model_name}")
```

> 后续若钉钉表格新增视频厂商/模型，只需在 `_resolve_provider` 加一个前缀判断 + 在 `VideoGenerator` 加一组 `_submit_x` / `_poll_x` 方法，**不改 toml**。

`Settings` 新增 `video_providers: dict[str, VideoProviderConfig]` / `video_tables: list[VideoTableConfig]` / `video_poll: VideoPollConfig` 字段，扩展 `_load_toml_config` 解析逻辑，新增 `get_video_table(key)` / `get_video_provider(provider_key)`（无 `get_video_model`）。

`configs/config.toml` 追加：

```toml
# 视频生成 — 厂商基础信息（与 model_name 解耦）
[ai.video.poll]
initial_wait = 10
interval = 5
max_total = 600

[ai.video_provider."kling"]
base_url = "https://api.vectorengine.ai"

[ai.video_provider."hailuo"]
base_url = "https://api.vectorengine.ai"

[ai.video_provider."wanxiang"]
base_url = "https://api.vectorengine.ai"

# 视频表 — 三个品牌
[[dingtalk.video_tables]]
key = "zhuozhi-video"
base_id = "<待补>"
sheet_id = "<待补>"
video_api_key_env = "ZHUOZHI_VIDEO_API_KEY"

[[dingtalk.video_tables]]
key = "huapu-video"
base_id = "<待补>"
sheet_id = "<待补>"
video_api_key_env = "HUAPU_VIDEO_API_KEY"

[[dingtalk.video_tables]]
key = "ahmi-video"
base_id = "<待补>"
sheet_id = "<待补>"
video_api_key_env = "AHMI_VIDEO_API_KEY"
```

> 钉钉表格"视频模型"列做成**下拉选项**，选项就是 `kling-v2-5-turbo` / `kling-v1-6` / `MiniMax-Hailuo-2.3` / `happyhorse-1.0-i2v` 等，服务读到啥就传给 API 啥。新增模型版本无需改服务（除非换厂商）。

### 4. API 路由（`src/api/router.py`）

在同一个 `router.py` 新增（不另开文件，与图片接口并列）：

```python
@router.post("/api/v1/video/generate", response_model=TaskResponse)
async def trigger_video_generation(
    req: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    service: VideoGenerationService = Depends(get_video_generation_service),
    _api_key: str = Depends(verify_api_key),
) -> TaskResponse:
    logger.info("收到 video generate 请求", record_id=req.record_id, table_key=req.table_key)
    background_tasks.add_task(service.process, req.record_id, req.table_key)
    return TaskResponse(
        status="accepted", message="视频生成任务已提交，处理完成后将更新表格",
        record_id=req.record_id,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
```

钉钉自动化在视频表中将 webhook 改指向 `/api/v1/video/generate`，请求体 `{record_id, table_key: "zhuozhi-video"}` 与图片侧完全对称。视频侧不另开 health 接口，沿用图片侧已有的健康检查即可。

### 5. 并发与超时考量

- **并发模型**：图片 Service 用 `settings.server.max_concurrency=5`，视频 Service 用 `settings.server.video.max_concurrency=3`，两个 Service 各自持有独立 Semaphore，系统总并发上限 = 8
- 轮询期间 `asyncio.sleep` 不占 CPU，但占 Semaphore 槽位。这是预期行为（限制对第三方 API 的并发压力）。

## 关键文件清单

需新增：
- `src/generator/video_engine.py`
- `src/services/video_generation.py`
- `src/models/video_request.py`
- `tests/test_generator/test_video_engine.py`
- `tests/test_services/test_video_generation.py`

需修改：
- `src/config.py`（新增视频相关 BaseModel 类、Settings 字段、toml 加载分支、`get_video_table` / `get_video_provider`；**不引入视频 model 配置类**，model_name 来自钉钉表格）
- `src/api/deps.py`（加 `get_video_generator` / `get_video_generation_service`）
- `src/api/router.py`（加 `POST /api/v1/video/generate` 路由）
- `src/dingtalk/client.py`（`upload_attachment` 加 `media_type` 入参，默认值保持向后兼容）
- `src/models/__init__.py`（导出 `VideoGenerateRequest`）
- `tests/conftest.py`（`app` fixture 补 `get_video_generator` / `get_video_generation_service` 两个 patch）
- `configs/config.toml`（追加视频厂商节 + 视频表节，无视频模型节）
- `configs/.env.example`（追加三个 `*_VIDEO_API_KEY` 占位）
- `pyproject.toml` / `requirements.txt` — 当前依赖（httpx + Pillow）已足够，无需新增 SDK
- `docs/ARCHITECTURE.md` / `docs/MODULES.md` / `docs/API.md` / `docs/ROADMAP.md` — 增补视频模块说明、新接口、长任务轮询架构图
- **此计划文件同步一份到 `docs/PLAN-video-generation.md`**（用户要求：项目文档目录下也存一份）

## 实施分阶段建议

1. **P0 配置 + 基础设施**：扩展 `config.py`、`upload_attachment` 加 `media_type` 入参、补充 toml 配置（不含 base_id）
2. **P1 单家最小可用**：先做 **Kling**（文档最完整、明确支持 base64）端到端跑通：submit → poll → 下载 → 上传钉钉 → 回写
3. **P2 加入 Hailuo + Wanxiang**：复用 P1 骨架，仅各家 `_submit_*` / `_poll_*` 适配字段差异
4. **P3 测试 + 文档**：单元测试覆盖三家路由、mock httpx；集成测试参考 `tests/test_upload_integration.py` 风格手动跑

## Verification（端到端验证）

按从上到下顺序验证：

1. **配置加载**：`python -c "from src.config import Settings; s = Settings(); print(s.get_video_table('zhuozhi-video')); print(s.get_video_provider('kling'))"`，使用 `D:\miniconda\envs\spider\python.exe`
2. **单元测试**：`pytest tests/test_generator/test_video_engine.py tests/test_services/test_video_generation.py -v`
3. **本地启动**：`uvicorn main:app --reload --host 0.0.0.0 --port 8030 --app-dir src`
4. **接口手工验证**：
   - `curl -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{"record_id":"<真实记录>","table_key":"zhuozhi-video"}' http://localhost:8030/api/v1/video/generate` → 202 accepted
5. **端到端**：在钉钉视频表上传首帧图 + 提示词 + 视频模型单元格填 `kling-v2-5-turbo`，点击触发按钮 → 观察后端日志按 9 步推进 → 等待 ~1-3 分钟 → 钉钉表格"生成视频"附件出现 mp4，"生成结果"="成功"
6. **失败路径**：删除"提示词"字段重试 → 表格"生成结果"显示 `失败: [校验提示词] 提示词不能为空`
7. **三家并行**：在不同视频表分别选 Kling / Hailuo / Wanxiang 各触发 1 次 → 全部成功
8. **超时/失败**：手动改 `max_total = 30` 模拟超时 → 表格回写"失败: [轮询任务] 视频任务超时"
