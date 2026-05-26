# API Key 统一调整方案

## 变更背景

目前系统为每个 AI 表格配置了两个独立的 API Key：
- `nanobanana_api_key_env`: 用于 Google provider 模型（Nano Banana Pro / Nano Banana 2）
- `gpt_image_api_key_env`: 用于 OpenAI provider 模型（GPT Image 2）

实际上两个 Key 来自同一个中转站，无需区分。统一使用单一 Key 可以简化配置管理。

## 变更目标

将 `nanobanana_api_key_env` 和 `gpt_image_api_key_env` 合并为 `image_api_key_env`，统一用于所有 AI 图片生成模型。

## 环境变量映射变更

| 原变量名 | 新变量名 |
|---------|---------|
| `ZHUOZHI_NANOBANANA_API_KEY` | `ZHUOZHI_IMAGE_API_KEY` |
| `ZHUOZHI_GPT_IMAGE_API_KEY` | `ZHUOZHI_IMAGE_API_KEY` |
| `AHMI_NANOBANANA_API_KEY` | `AHMI_IMAGE_API_KEY` |
| `AHMI_GPT_IMAGE_API_KEY` | `AHMI_IMAGE_API_KEY` |
| `HUAPU_NANOBANANA_API_KEY` | `HUAPU_IMAGE_API_KEY` |
| `HUAPU_GPT_IMAGE_API_KEY` | `HUAPU_IMAGE_API_KEY` |

## 详细变更步骤

### 1. 修改 `src/config.py`

**位置**: `TableConfig` 类（第 28-41 行）

```python
# 删除以下两行：
gpt_image_api_key_env: str  # 必填，指定使用的 GPT Image API Key 环境变量名
nanobanana_api_key_env: str  # 必填，指定使用的 NanoBanana API Key 环境变量名

# 替换为一行：
image_api_key_env: str  # 必填，指定使用的 AI 图片 API Key 环境变量名
```

### 2. 修改 `src/generator/engine.py`

**位置**: `_do_generate` 内部函数（第 69-75 行）

```python
# 原代码：
api_key = self.settings.get_api_key(
    table_config.nanobanana_api_key_env
    if model_cfg.provider == "google"
    else table_config.gpt_image_api_key_env
)

# 替换为：
api_key = self.settings.get_api_key(table_config.image_api_key_env)
```

同时更新函数文档（第 64 行）：

```python
# 原文档：
# table_config: 表格配置，用于获取对应的 API Key 环境变量名。必填，缺失将导致无法获取 API Key。

# 替换为：
# table_config: 表格配置，通过 api_key_env 字段获取 API Key 环境变量名。必填。
```

### 3. 修改 `configs/config.toml`

对每个表格配置块（zhuozhi-base、huapu-base、ahmi-base）进行以下替换：

```toml
# 删除以下两行：
gpt_image_api_key_env = "ZHUOZHI_GPT_IMAGE_API_KEY"
nanobanana_api_key_env = "ZHUOZHI_NANOBANANA_API_KEY"

# 替换为一行（根据表格前缀选择）：
# zhuozhi-base 使用：
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"

# huapu-base 使用：
image_api_key_env = "HUAPU_IMAGE_API_KEY"

# ahmi-base 使用：
image_api_key_env = "AHMI_IMAGE_API_KEY"
```

### 4. 修改 `.env.example` 和 `configs/.env.example`

**位置**: 环境变量定义部分

```bash
# 删除以下四行：
# ZHUOZHI_NANOBANANA_API_KEY=your-zhuozhi-nanobanana-key
# ZHUOZHI_GPT_IMAGE_API_KEY=your-zhuozhi-gpt-image-key
# AHMI_NANOBANANA_API_KEY=your-ahmi-nanobanana-key
# AHMI_GPT_IMAGE_API_KEY=your-ahmi-gpt-image-key
# HUAPU_NANOBANANA_API_KEY=your-huapu-nanobanana-key
# HUAPU_GPT_IMAGE_API_KEY=your-huapu-gpt-image-key

# 替换为三行：
ZHUOZHI_IMAGE_API_KEY=your-zhuozhi-image-key
AHMI_IMAGE_API_KEY=your-ahmi-image-key
HUAPU_IMAGE_API_KEY=your-huapu-image-key

# 同时更新注释：
# 每个表格在 config.toml 中通过 api_key_env 指定环境变量名
```

### 5. 修改 `.github/workflows/deploy.yml`

**位置**: GitHub Actions secrets 映射（第 74-75 行）

```yaml
# 删除以下两行：
# ZHUOZHI_NANOBANANA_API_KEY: ${{ secrets.ZHUOZHI_NANOBANANA_API_KEY }}
# ZHUOZHI_GPT_IMAGE_API_KEY: ${{ secrets.ZHUOZHI_GPT_IMAGE_API_KEY }}

# 替换为一行：
ZHUOZHI_IMAGE_API_KEY: ${{ secrets.ZHUOZHI_IMAGE_API_KEY }}
```

**注意**: 需要在 GitHub 仓库 Settings → Secrets 中更新 secrets，删除旧的两个，添加新的 `ZHUOZHI_IMAGE_API_KEY`。

### 6. 修改 `tests/conftest.py`

**位置**: 测试配置（第 56-57 行）

```python
# 原代码：
gpt_image_api_key_env="ZHUOZHI_GPT_IMAGE_API_KEY",
nanobanana_api_key_env="ZHUOZHI_NANOBANANA_API_KEY",

# 替换为：
image_api_key_env="ZHUOZHI_IMAGE_API_KEY",
```

### 7. 更新 `docs/DEPLOYMENT.md`

**位置 1**: 环境变量示例（第 84-89 行）

```markdown
# 删除：
# ZHUOZHI_NANOBANANA_API_KEY=your-zhuozhi-nanobanana-key
# ZHUOZHI_GPT_IMAGE_API_KEY=your-zhuozhi-gpt-image-key
# AHMI_NANOBANANA_API_KEY=your-ahmi-nanobanana-key
# AHMI_GPT_IMAGE_API_KEY=your-ahmi-gpt-image-key
# HUAPU_NANOBANANA_API_KEY=your-huapu-nanobanana-key
# HUAPU_GPT_IMAGE_API_KEY=your-huapu-gpt-image-key

# 替换为：
ZHUOZHI_IMAGE_API_KEY=your-zhuozhi-image-key
AHMI_IMAGE_API_KEY=your-ahmi-image-key
HUAPU_IMAGE_API_KEY=your-huapu-image-key
```

**位置 2**: config.toml 示例（第 105-106 行）

```toml
# 删除：
# gpt_image_api_key_env = "ZHUOZHI_GPT_IMAGE_API_KEY"
# nanobanana_api_key_env = "ZHUOZHI_NANOBANANA_API_KEY"

# 替换为：
image_api_key_env = "ZHUOZHI_IMAGE_API_KEY"
```

**位置 3**: 环境变量表格（第 223-228 行）

```markdown
# 删除以下四行：
# | `ZHUOZHI_NANOBANANA_API_KEY` | .env | 卓智 NanoBanana API Key |
# | `ZHUOZHI_GPT_IMAGE_API_KEY`  | .env | 卓智 GPT Image API Key |
# | `AHMI_NANOBANANA_API_KEY`    | .env | AHMI NanoBanana API Key |
# | `AHMI_GPT_IMAGE_API_KEY`     | .env | AHMI GPT Image API Key |
# | `HUAPU_NANOBANANA_API_KEY`   | .env | 华普 NanoBanana API Key |
# | `HUAPU_GPT_IMAGE_API_KEY`    | .env | 华普 GPT Image API Key |

# 替换为三行：
| `ZHUOZHI_IMAGE_API_KEY` | .env | 卓智 AI 图片 API Key |
| `AHMI_IMAGE_API_KEY`    | .env | AHMI AI 图片 API Key |
| `HUAPU_IMAGE_API_KEY`   | .env | 华普 AI 图片 API Key |
```

### 8. 更新 `docs/ROADMAP.md`

**位置**: 环境变量说明（第 25 行）

```markdown
# 删除：
# `.env`（敏感）：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_ID`、`API_KEY`、`ZHUOZHI_NANOBANANA_API_KEY`、`ZHUOZHI_GPT_IMAGE_API_KEY`、`AHMI_NANOBANANA_API_KEY`、`AHMI_GPT_IMAGE_API_KEY`、`HUAPU_NANOBANANA_API_KEY`、`HUAPU_GPT_IMAGE_API_KEY`

# 替换为：
`.env`（敏感）：`DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`、`DINGTALK_OPERATOR_ID`、`API_KEY`、`ZHUOZHI_IMAGE_API_KEY`、`AHMI_IMAGE_API_KEY`、`HUAPU_IMAGE_API_KEY`
```

### 9. 更新 `docs/WORKFLOW.md`

**位置**: 环境变量示例（第 294-303 行）

```markdown
# 删除以下六行：
# ZHUOZHI_NANOBANANA_API_KEY=your-zhuozhi-nanobanana-key
# ZHUOZHI_GPT_IMAGE_API_KEY=your-zhuozhi-gpt-image-key
# AHMI_NANOBANANA_API_KEY=your-ahmi-nanobanana-key
# AHMI_GPT_IMAGE_API_KEY=your-ahmi-gpt-image-key
# HUAPU_NANOBANANA_API_KEY=your-huapu-nanobanana-key
# HUAPU_GPT_IMAGE_API_KEY=your-huapu-gpt-image-key

# 替换为三行：
ZHUOZHI_IMAGE_API_KEY=your-zhuozhi-image-key
AHMI_IMAGE_API_KEY=your-ahmi-image-key
HUAPU_IMAGE_API_KEY=your-huapu-image-key
```

### 10. 更新本地 `.env` 文件

**操作**: 根据实际 Key 值更新本地开发环境

```bash
# 假设原 .env 中有：
# ZHUOZHI_NANOBANANA_API_KEY=xxx
# ZHUOZHI_GPT_IMAGE_API_KEY=xxx

# 两个值相同的情况下，使用任一值：
ZHUOZHI_IMAGE_API_KEY=xxx
```

## 验证方法

### 1. 本地验证

```bash
# 1. 更新 .env 文件
# 2. 运行类型检查
python -m mypy src/

# 3. 运行测试
pytest tests/

# 4. 启动服务
python -m uvicorn main:app --reload

# 5. 手动测试生图接口
```

### 2. 部署验证

1. 合并代码后，GitHub Actions 自动部署
2. 在钉钉表格中测试生图功能
3. 验证 GPT Image 2 和 Nano Banana 模型均可正常工作

## 回滚方案

如需回滚，按以下步骤操作：

1. 恢复所有被修改的文件到变更前版本
2. 恢复 GitHub Secrets（`ZHUOZHI_NANOBANANA_API_KEY`、`ZHUOZHI_GPT_IMAGE_API_KEY`）
3. 部署回滚

## 影响评估

- **破坏性变更**: 是，需要同步更新 `.env` 和 GitHub Secrets
- **向后兼容**: 否
- **风险等级**: 中（变更涉及核心配置加载逻辑）

## 实施顺序建议

1. 先修改代码和配置文件（步骤 1-3、6）
2. 本地验证通过后提交
3. 更新文档（步骤 4、7-9）
4. 更新 GitHub Secrets 和部署配置（步骤 5）
5. 部署验证
6. 更新本地 `.env` 文件（步骤 10）