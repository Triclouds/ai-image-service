# API 接口文档

## 基础信息

- **Base URL**: `http://localhost:8030`
- **Content-Type**: `application/json`
- **OpenAPI 文档**: `http://localhost:8030/docs`（自动生成）

---

## 1. 触发图片生成

钉钉表格自动化流程调用的入口接口。

```
POST /api/v1/generate
```

### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `record_id` | string | 是 | 钉钉多维表格的记录ID |
| `table_key` | string | 否 | 表格配置 key，默认使用 `config.toml` 中的 `default_table` |

### 请求示例

```json
{
    "record_id": "rec_aBcDeFg12345",
    "table_key": "clothing"
}
```

### 响应

**202 Accepted** — 任务已接收，后台处理中

```json
{
    "status": "accepted",
    "message": "任务已提交，处理完成后将更新表格",
    "record_id": "rec_aBcDeFg12345",
    "timestamp": "2026-05-15 10:30"
}
```

**400 Bad Request** — 参数校验失败

```json
{
    "detail": "record_id 不能为空"
}
```

**401 Unauthorized** — API Key 无效

```json
{
    "detail": "Invalid API key"
}
```

**500 Internal Server Error** — 服务异常

```json
{
    "detail": "Internal server error"
}
```

---

## 2. 健康检查

```
GET /api/v1/health
```

### 响应

```json
{
    "status": "ok",
    "version": "0.1.0"
}
```

---

## 鉴权机制

所有 `/api/v1/generate` 请求需携带 API Key：

```
Authorization: Bearer <API_KEY>
```

API Key 通过环境变量 `API_KEY` 配置，在钉钉自动化流程的 HTTP 请求中设置 Header。

---

## 4. 错误处理

### 4.1 错误分类原则

| 错误类型 | HTTP 响应 | 回写表格 | 说明 |
|----------|-----------|----------|------|
| 请求格式错误（缺少 record_id 等） | 400 | 否 | 请求参数不合法 |
| 鉴权失败（API Key 无效） | 401 | 否 | 请求被拒绝 |
| 服务异常（钉钉/AI 接口报错） | 202 Accepted | 是（"失败: xxx"） | 业务执行失败，统一回写表格 |
| 超时/网络问题 | 202 Accepted | 是（"失败: xxx"） | 同上 |

**错误处理策略**：
- **同步校验错误**（参数格式、API Key 无效）→ 立即返回 4xx
- **异步业务错误**（记录不存在、AI 失败、下载失败等）→ 统一回写表格"失败: xxx"，HTTP 始终返回 202 Accepted

### 4.2 业务错误统一处理

所有业务执行中的错误（素材图下载失败、AI 生图超时、钉钉接口报错等），统一：
- HTTP 返回 202 Accepted（任务接收成功）
- 回写表格"生成结果"字段为 `"失败: {错误信息}"`

示例：AI 生图超时

```
// HTTP 202 Accepted
// 钉钉表格 "生成结果" 字段写入：
"失败: AI 生图超时，请稍后重试"
```

### 4.3 错误码

| 状态码 | 场景 | 说明 |
|--------|------|------|
| 400 | 参数校验失败 | 缺少 record_id 等 |
| 401 | API Key 无效 | 鉴权失败 |
| 202 Accepted | 任务已接收 | 后台处理中，结果看表格"生成结果"字段 |

---

## 5. 回调通知

生图完成后，结果直接写入钉钉表格，不再单独回调。如果需要额外的通知（如钉钉群消息），可在后续迭代中扩展。

### 钉钉表格字段映射

| 表格字段 | 更新内容 |
|----------|----------|
| 生成图片 | 生成的图片文件（附件），格式参考 `docs/sdk_docs/上传附件.md` |
| 生成结果 | `"成功"` 或 `"失败: {错误信息}"` |
| 生成时间 | 当前时间（`"YYYY-MM-DD HH:mm"` 字符串） |
