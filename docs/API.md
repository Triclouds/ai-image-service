# API 接口文档

## 基础信息

- **Base URL**: `http://localhost:8000`
- **Content-Type**: `application/json`
- **OpenAPI 文档**: `http://localhost:8000/docs`（自动生成）

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
| `table_id` | string | 否 | 表格ID（如不传则使用配置中的默认表格） |

### 请求示例

```json
{
    "record_id": "rec_aBcDeFg12345",
    "table_id": "tbl_xyz"
}
```

### 响应

**202 Accepted** — 任务已接收，后台处理中

```json
{
    "status": "accepted",
    "message": "任务已提交，处理完成后将更新表格",
    "record_id": "rec_aBcDeFg12345",
    "timestamp": "2026-05-15T10:30:00Z"
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

**429 Too Many Requests** — 请求过于频繁

```json
{
    "detail": "请求过于频繁，请稍后再试"
}
```

---

## 2. 查询任务状态（待实现）

```
GET /api/v1/tasks/{task_id}
```

### 响应

```json
{
    "task_id": "uuid-xxxx",
    "status": "processing" | "completed" | "failed",
    "record_id": "rec_aBcDeFg12345",
    "result": "成功" | "失败: 错误信息",
    "created_at": "2026-05-15T10:30:00Z",
    "completed_at": null
}
```

---

## 3. 健康检查

```
GET /api/v1/health
```

### 响应

```json
{
    "status": "ok",
    "version": "0.1.0",
    "dingtalk_connected": true
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

## 回调通知

生图完成后，结果直接写入钉钉表格，不再单独回调。如果需要额外的通知（如钉钉群消息），可在后续迭代中扩展。

### 钉钉表格字段映射

| 表格字段 | 更新内容 |
|----------|----------|
| 生成图片 | 生成的图片文件（附件） |
| 生成结果 | `"成功"` 或 `"失败: {错误信息}"` |
| 生成时间 | ISO 8601 格式时间戳 |
