# 开发工作流

## 1. 环境准备

### 1.1 前提条件

- Python 3.11+
- conda 环境：`D:\miniconda\envs\spider\python.exe`
- Git
- 钉钉开发者账号（需创建应用获取 AppKey/AppSecret）

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
cp configs/.env.example configs/.env
# 编辑 configs/.env，填入真实配置
```

### 1.4 钉钉应用配置

1. 登录 [钉钉开放平台](https://open.dingtalk.com/)
2. 创建企业内部应用 → 获取 AppKey / AppSecret
3. 配置权限：
   - 多维表格读写 (`qyapi_table_read`, `qyapi_table_write`)
   - 云空间文件上传 (`qyapi_drive_write`)
4. 发布应用

### 1.5 钉钉AI表格配置

1. 在钉钉中创建多维表格，字段如下：

   | 字段名 | 字段类型 | 说明 |
   |--------|----------|------|
   | 编号 | 自动编号 | 无需手动填写 |
   | 款号 | 文本 | 由协作者填写 |
   | 素材图 | 附件 | 由协作者上传 |
   | 提示词 | 文本 | 由协作者填写 |
   | 生图模型 | 单选 | 预设选项：Flux.1 Pro、DALL-E 3、SDXL 等 |
   | 生成图片 | 附件 | 系统自动回写 |
   | 生成结果 | 文本 | 系统自动回写 |
   | 生成时间 | 日期 | 系统自动回写 |
   | 生成按钮 | 按钮 | 触发自动化 |

2. 配置自动化规则：
   - 触发器：点击"生成按钮"
   - 动作：发送 HTTP 请求到 `POST http://<your-server>:8000/api/v1/generate`
   - 请求体：`{"record_id": "{{record_id}}"}`
   - Header：`Authorization: Bearer <API_KEY>`

---

## 2. 日常开发

### 2.1 启动开发服务器

```bash
cd F:\MyCode\PythonProject\ai_gen_image
conda activate spider
uvicorn ai_gen_image.main:app --reload --host 0.0.0.0 --port 8000
```

### 2.2 运行测试

```bash
# 全部测试
pytest

# 指定模块
pytest tests/test_services/

# 带覆盖率
pytest --cov=src/ai_gen_image --cov-report=html
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
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"record_id": "rec_test_001"}'
```

---

## 3. Git 工作流

### 3.1 分支策略

```
master  ──────── 稳定发布
  │
  └─ feature/xxxx ── 功能开发
  └─ fix/xxxx     ── Bug修复
  └─ docs/xxxx    ── 文档更新
```

### 3.2 提交规范

使用 Conventional Commits：

```
feat(ddclient): 实现钉钉多维表格记录读写
fix(generator): 修复 Flux API 超时未重试的问题
docs(api): 补充错误码说明
refactor(services): 将生图流程抽为可插拔 Pipeline
test(generation): 添加端到端生图流程测试
chore(deps): 更新 httpx 到 0.28.x
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
# 正确：使用包内相对导入
from ai_gen_image.services.generation import GenerationService
from ai_gen_image.models.request import GenerateRequest

# 正确：同级模块直接用相对路径
from .client import DingTalkClient
from .models import DingTalkRecord
```

### 4.2 异步优先

所有 I/O 操作（HTTP请求、文件读写、数据库查询）必须使用 `async/await`，避免阻塞事件循环。

```python
# 正确
async def get_record(self, table_id: str, record_id: str) -> dict:
    response = await self.http.get(f"/v1.0/table/{table_id}/records/{record_id}")
    return response.json()

# 错误 — 会阻塞整个事件循环
def get_record(self, table_id: str, record_id: str) -> dict:
    response = requests.get(f"https://api.dingtalk.com/v1.0/table/{table_id}/records/{record_id}")
    return response.json()
```

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
    mock_dd.get_record.return_value = {"提示词": "A cat", "生图模型": "flux"}
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
# configs/.env
DINGTALK_APP_KEY=dingxxxxxxxxxxxx
DINGTALK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxx
DINGTALK_TABLE_ID=tbl_xxxxxxxx
DINGTALK_SPACE_ID=spc_xxxxxxxx
API_KEY=your-secret-api-key
DEFAULT_MODEL=flux-1.1-pro
OPENAI_API_KEY=sk-xxxxxxxx
```
