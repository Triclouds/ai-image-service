# 部署文档

## 1. 开发环境（Windows）

### 1.1 环境准备

- Python 3.11+
- conda 环境：`D:\miniconda\envs\spider\python.exe`
- Git

### 1.2 启动开发服务器

```bash
cd F:\MyCode\PythonProject\ai_gen_image
conda activate spider
uvicorn main:app --reload --host 0.0.0.0 --port 8030 --app-dir src
```

### 1.3 本地调试钉钉回调

使用 `curl` 模拟钉钉自动化请求：

```bash
curl -X POST http://localhost:8030/api/v1/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"record_id": "rec_test_001"}'
```

---

## 2. 生产部署（Linux）

### 2.1 部署方式

使用 GitHub Actions 进行服务器部署。

#### 部署流程

```
GitHub Push/Merge → GitHub Actions 触发 → 构建 → 部署到 Linux 服务器
```

#### GitHub Actions 配置

部署配置位于 `.github/workflows/deploy.yml`：

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to server
        run: |
          # 通过 SSH 部署到 Linux 服务器
          # 具体命令根据实际服务器配置
```

## 3. 环境配置

### 3.1 敏感配置（.env）

通过环境变量注入，**不要**提交到代码库：

```
# 钉钉配置
DINGTALK_APP_KEY=dingxxxxxxxxxxxx
DINGTALK_APP_SECRET=xxxxxxxxxxxxxxxx
DINGTALK_OPERATOR_ID=operator_union_id

# API 鉴权
API_KEY=your-secret-api-key

# AI 模型 Key（每个表格在 config.toml 中通过 image_api_key_env 指定）
ZHUOZHI_IMAGE_API_KEY=your-zhuozhi-image-key
AHMI_IMAGE_API_KEY=your-ahmi-image-key
HUAPU_IMAGE_API_KEY=your-huapu-image-key
```

### 3.2 非敏感配置（config.toml）

提交到代码库：

```toml
# config.toml
[dingtalk]
default_table = "clothing"

[[dingtalk.tables]]
key = "clothing"
base_id = "tbl_xxxxxxxx"
sheet_id = "sheet_xxx"
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"
prompt_field = "提示词"
model_field = "生图模型"
reference_image_field = "素材图"
result_image_field = "生成图片"
result_status_field = "生成结果"
result_time_field = "生成时间"

[server]
host = "0.0.0.0"
port = 8030
log_level = "INFO"
max_concurrency = 5

[ai]
default_model = "Nano Banana 2"
base_url = "https://api.relayrouter.app"

[ai.model."Nano Banana Pro"]
endpoint = "/v1beta/models/gemini-3-pro-image-preview:generateContent"
model_name = "gemini-3-pro-image-preview"
provider = "google"

[ai.model."Nano Banana 2"]
endpoint = "/v1beta/models/gemini-3.1-flash-image-preview:generateContent"
model_name = "gemini-3.1-flash-image-preview"
provider = "google"

[ai.model."GPT Image 2"]
endpoint = "/v1/images/edits"
model_name = "gpt-image-2"
provider = "openai"
```

**多表格扩展**：新增表格时在 `[[dingtalk.tables]]` 数组中追加一段配置，修改 `default_table` 为新表格 key 即可。每个表格的字段映射独立定义，代码不硬编码。

---

## 4. 出站代理

AI 中转站 `api.relayrouter.app` 需要海外出口，国内直连会超时（curl 约 20s timeout）。

代理通过环境变量 `PROXY_URL` 配置（写在 `.env`），**仅作用于 AI / 视频中转站调用**；钉钉接口保持直连（`trust_env=False`），不走代理。

| 变量 | 示例 | 说明 |
|------|------|------|
| `PROXY_URL` | `http://127.0.0.1:7897` | 代理地址。留空 = 不强制，沿用系统/环境代理（httpx `trust_env`） |

### 4.1 本地开发（Windows + Clash Verge）

1. Clash Verge → 订阅 → 粘贴机场订阅链接 → 更新
2. 选择节点（优先美/日/新机房；避开被 AI 平台风控的 IP）
3. 保持「系统代理」开启（默认 mixed 端口 7897，HTTP/SOCKS5 同端口）
4. 在 `.env` 写死代理，避免受系统代理开关影响：
   ```
   PROXY_URL=http://127.0.0.1:7897
   ```
5. 验证：
   ```bash
   curl -x http://127.0.0.1:7897 -o /dev/null -w "%{http_code}\n" https://api.relayrouter.app/
   ```
   期望 `200`。

> httpx 默认 `trust_env=True`，在 Windows 上会自动读取注册表里的系统代理，所以本地即使不配 `PROXY_URL` 也能走代理；显式配置只是让行为确定、且不影响钉钉直连。

### 4.2 生产部署（Docker）

生产是 Docker 容器（`1panel-network`，172.18.0.15），容器内没有「系统代理」概念，必须显式配置。

`docker-compose.yml` 已用 `env_file: .env` 注入全部环境变量，**无需改 compose**，只需：

1. 在 GitHub Secrets 新增 `PROXY_URL`（如 `http://mihomo:7890`）；不设置则为空 = 保持直连行为不变
2. 服务器上要有可用的代理服务。推荐在同一 compose 栈里加 mihomo 边车：

   ```yaml
   services:
     mihomo:
       image: metacubex/mihomo:latest
       container_name: mihomo
       volumes:
         - ./mihomo:/root/.config/mihomo
       restart: unless-stopped
       networks:
         1panel-network:
           ipv4_address: 172.18.0.16
   ```

   `mihomo/config.yaml`（订阅链接含 token，**不要提交到仓库**，用 Secret 注入）：

   ```yaml
   mixed-port: 7890
   allow-lan: true
   mode: rule
   proxy-providers:
     airport:
       type: http
       url: "<机场订阅链接>"
       path: ./providers/airport.yaml
       interval: 3600
       health-check:
         enable: true
         url: https://www.gstatic.com/generate_204
         interval: 300
   proxy-groups:
     - name: PROXY
       type: select
       use: [airport]
   rules:
     - MATCH,PROXY
   ```

3. 服务器 `.env` 中 `PROXY_URL=http://mihomo:7890`（容器内用服务名，不是 127.0.0.1）

### 4.3 排错

```bash
# 容器内验证代理是否可用
docker exec ai-image-service python -c "import httpx; print(httpx.get('https://api.relayrouter.app/', timeout=30).status_code)"
```

- 超时 → 代理地址/节点不通，检查 mihomo 日志 `docker logs mihomo`
- 401/403 → 代理通了，是 API Key 问题
- 订阅过期 → 机场侧续费；`proxy-providers` 的 `interval` 会自动拉取新配置

---

## 5. 日志管理

### 5.1 日志轮转配置

使用 loguru 的日志轮转，每天一次，最多保留 10 天。

代码中的配置（`src/config.py`）：

```python
from loguru import logger

# 日志轮转：每天凌晨 0 点轮转，保留 10 天
logger.add(
    "logs/app_{time}.log",
    rotation="00:00",      # 每天 0 点轮转
    retention="10 days",   # 保留 10 天
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
)
```

### 5.2 日志目录

```
ai_gen_image/
├── src/                   # 源代码
├── logs/                  # 日志目录
│   ├── app_2026-05-15.log
│   ├── app_2026-05-14.log
│   └── ...
└── .gitignore
```

`.gitignore` 中忽略日志目录：

```
logs/
*.log
```

### 5.3 日志级别

| 环境 | 级别 | 说明 |
|------|------|------|
| 生产 | INFO | 记录关键节点 |
| 开发 | DEBUG | 记录详细调试信息 |

---

## 6. 健康检查

### 6.1 健康检查端点

```
GET /api/v1/health
```

响应：

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

---

## 7. 环境变量参考

完整环境变量列表：

| 变量名 | 来源 | 说明 |
|--------|------|------|
| `DINGTALK_APP_KEY` | .env | 钉钉 AppKey |
| `DINGTALK_APP_SECRET` | .env | 钉钉 AppSecret |
| `DINGTALK_OPERATOR_ID` | .env | 操作人 unionId |
| `API_KEY` | .env | API 鉴权 Key |
| `AI_BASE_URL` | .env/.toml | AI 模型中转站地址，所有模型共用 |
| `ZHUOZHI_IMAGE_API_KEY` | .env | 卓智 AI 图片 API Key |
| `AHMI_IMAGE_API_KEY` | .env | AHMI AI 图片 API Key |
| `HUAPU_IMAGE_API_KEY` | .env | 华普 AI 图片 API Key |
| `CONFIG_PATH` | .env | 配置文件路径，默认 `configs/config.toml` |
| `PROXY_URL` | .env | 出站代理地址（仅 AI/视频中转站调用），留空则沿用系统/环境代理 |