# 开发工作流

## 1. 环境准备

### 1.1 前提条件

- Python 3.11+
- conda 环境：`D:\miniconda\envs\spider\python.exe`
- Git
- 钉钉开发者账号（需创建应用获取 AppKey/AppSecret）
- AI 模型中转站账号（获取各模型的 API Key）

### 1.2 初始化项目

```bash
# 克隆仓库（如果是新项目）
cd F:\MyCode\PythonProject\ai_gen_image

# 激活 conda 环境
conda activate spider

# 安装依赖
pip install -e ".[dev]"
```

### 1.3 配置文件

```bash
# 从模板创建环境变量文件
copy configs\.env.example .env
# 编辑 .env，填入真实配置
```

### 1.4 钉钉应用配置

1. 登录 [钉钉开放平台](https://open.dingtalk.com/)
2. 创建企业内部应用 → 获取 AppKey / AppSecret
3. 配置权限：
   - 多维表格读写 (`dingtalk.tables.*`)
   - 云空间文件上传 (`dingtalk.drive.*`)
4. 发布应用

### 1.5 钉钉AI表格配置

1. 在钉钉中创建多维表格，字段如下：

   | 字段名 | 字段类型 | 说明 |
   |--------|----------|------|
   | 编号 | 自动编号 | 无需手动填写 |
   | 款号 | 文本 | 由协作者填写 |
   | 素材图 | 附件 | 由协作者上传（图生图参考图） |
   | 提示词 | 文本 | 由协作者填写 |
   | 生图模型 | 单选 | 预设选项：Nano Banana Pro、Nano Banana 2、GPT Image 2 |
   | 生成图片 | 附件 | 系统自动回写 |
   | 生成结果 | 文本 | 系统自动回写 |
   | 生成时间 | 日期 | 系统自动回写 |
   | 生成按钮 | 按钮 | 触发自动化 |

2. 配置自动化规则：
   - 触发器：点击"生成按钮"
   - 动作：发送 HTTP 请求到 `POST http://<your-server>:8030/api/v1/generate`
   - 请求体：`{"record_id": "{{record_id}}"}`
   - Header：`Authorization: Bearer <API_KEY>`

---

## 2. 日常开发

### 2.1 启动开发服务器

```bash
cd F:\MyCode\PythonProject\ai_gen_image
conda activate spider
uvicorn main:app --reload --host 0.0.0.0 --port 8030 --app-dir src
```

### 2.2 运行测试

```bash
# 全部测试
pytest

# 指定模块
pytest tests/test_services/

# 带覆盖率
pytest --cov=src --cov-report=html
```

### 2.3 代码质量

```bash
# 格式化
ruff format src/ tests/

# 检查
ruff check src/ tests/

# 类型检查
mypy src/
```

### 2.4 本地调试钉钉回调

使用 `curl` 模拟钉钉自动化请求：

```bash
curl -X POST http://localhost:8030/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"record_id": "rec_test_001"}'
```

---

## 3. Git 工作流

### 3.1 分支策略

```
main  ──────── 稳定发布
  │
  └─ feature/xxxx ── 功能开发
  └─ fix/xxxx     ── Bug修复
  └─ docs/xxxx    ── 文档更新
```

### 3.2 提交规范

使用 Conventional Commits：

```
feat(dingtalk): 实现钉钉多维表格记录读写
fix(generator): 修复 NanoBanana API 超时未重试
feat(generator): 添加 GPT-Image 引擎支持
docs(api): 补充错误码说明
refactor(services): 将生图流程抽为可插拔 Pipeline
test(generation): 添加端到端生图流程测试
chore(deps): 更新钉钉 SDK 版本
```

### 3.3 提交前检查

```bash
# 格式化代码
ruff format src/ tests/
ruff check src/ tests/ --fix

# 运行测试
pytest

# 如果有类型检查
mypy src/
```

---

## 4. 项目结构约定

### 4.1 导入风格

```python
# 正确：直接导入模块
from services.generation import GenerationService
from models.request import GenerateRequest

# 正确：同级模块直接导入
from dingtalk.client import DingTalkClient
```

### 4.2 异步优先（强制）

**全流程采用异步操作，禁止任何同步阻塞。** 所有 I/O 操作（HTTP请求、文件读写、数据库查询）必须使用 `async/await`，避免阻塞事件循环。

```python
# 正确 — 异步 HTTP 请求
async def get_record(self, record_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"/table/{record_id}")
        return response.json()

# 正确 — 钉钉 SDK 异步方法
response = await client.get_record_with_options_async(
    base_id, sheet_id, record_id, request, headers, runtime
)

# 错误 — 会阻塞整个事件循环
def get_record(self, record_id: str) -> dict:
    response = requests.get(f"https://api.dingtalk.com/table/{record_id}")
    return response.json()

# 错误 — 同步 sleep 阻塞事件循环
import time
time.sleep(2)  # ❌ 禁止

# 正确 — 异步等待
import asyncio
await asyncio.sleep(2)  # ✅
```

**异步规范清单**：
- ✅ 钉钉 SDK 统一使用 `*_async` 方法（如 `get_record_with_options_async`）
- ✅ HTTP 请求使用 `httpx.AsyncClient`
- ✅ 文件读写使用 `aiofiles`（如需本地临时文件）
- ✅ 延迟等待使用 `asyncio.sleep()`，禁止 `time.sleep()`
- ✅ 并发控制使用 `asyncio.Semaphore`
- ✅ 重试使用 `tenacity`（`@retry(stop=stop_after_attempt(2), wait=wait_fixed(2))`）
- ❌ 禁止使用 `requests`、`time.sleep`、同步文件操作
- ❌ 禁止使用 Celery（用 asyncio 后台任务代替）
- ❌ 禁止手写 `while` 循环实现重试

### 4.3 异常处理

```python
# 模块内部定义专用异常
class DingTalkAPIError(Exception):
    """钉钉API调用异常。"""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code

class GenerationError(Exception):
    """生图过程异常。"""
    pass
```

### 4.4 日志规范

```python
from loguru import logger

# 关键节点日志
logger.info(f"开始处理 record_id={record_id}")
logger.info(f"生图完成，耗时 {elapsed:.2f}s")
logger.error(f"生图失败 record_id={record_id}: {e}")

# 调试日志
logger.debug(f"API 响应: {response.text}")
```

---

## 5. 测试策略

### 5.1 单元测试

每个模块独立测试，mock 外部依赖。

```python
# tests/test_services/test_generation.py
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_process_success():
    mock_dd = AsyncMock()
    mock_dd.get_record.return_value = {"提示词": "A cat", "生图模型": "Nano Banana Pro"}
    # ...
```

### 5.2 集成测试

使用真实钉钉测试应用进行端到端验证。

### 5.3 测试覆盖率目标

- 业务逻辑层（services）: ≥ 80%
- 钉钉客户端（dingtalk）: ≥ 70%
- API 路由层（api）: ≥ 60%

---

## 6. 环境变量模板

```ini
# .env

# ========== 钉钉配置 ==========
DINGTALK_APP_KEY=dingxxxxxxxxxxxx
DINGTALK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxx
DINGTALK_OPERATOR_ID=your_operator_unionid

# ========== 服务配置 ==========
API_KEY=your-secret-api-key

# ========== AI 生图配置（通过中转站）==========
# 所有模型共用同一个中转站 base_url
AI_BASE_URL=https://api.vectorengine.ai

# AI 图片模型 Key（每个表格在 config.toml 中通过 image_api_key_env 指定）
# 卓智
ZHUOZHI_IMAGE_API_KEY=your-zhuozhi-image-key

# AHMI
AHMI_IMAGE_API_KEY=your-ahmi-image-key

# 华普
HUAPU_IMAGE_API_KEY=your-huapu-image-key

# ========== 默认配置 ==========
DEFAULT_MODEL=Nano Banana 2
```

---

## 7. AI 模型说明

### 7.1 NanoBanana 系列

- **模型**: Nano Banana Pro、Nano Banana 2
- **SDK**: Google 官方 SDK
- **调用方式**: 通过中转站（base_url 可配置）
- **支持功能**: 文生图、图生图（img2img）

### 7.2 GPT-Image 系列

- **模型**: GPT-Image-2
- **SDK**: OpenAI 官方 SDK
- **调用方式**: 通过中转站（base_url 可配置）
- **支持功能**: 文生图、图生图（img2img）

### 7.3 模型选择

钉钉表格中"生图模型"字段支持以下值：

| 字段值 | 对应引擎 | 说明 |
|--------|----------|------|
| `Nano Banana Pro` | NanoBananaEngine | Google NanoBanana Pro |
| `Nano Banana 2` | NanoBananaEngine | Google NanoBanana 2 |
| `GPT Image 2` | AIGenerator (OpenAI SDK) | OpenAI GPT-Image 2 |

钉钉表格中"生图模型"字段为单选类型，预设选项：Nano Banana Pro、Nano Banana 2、GPT Image 2。
