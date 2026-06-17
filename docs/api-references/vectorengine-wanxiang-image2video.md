# 通义万象（Wanxiang）视频生成 — 图生视频 API

> 来源：https://vectorengine.apifox.cn/api-456406615
> 中转域名：`https://api.vectorengine.ai`
> Model: `happyhorse-1.0-i2v`

## 提交视频生成任务

**Endpoint**：`POST /alibailian/api/v1/services/aigc/video-generation/video-synthesis`

### Header 参数

| 字段 | 必需 | 示例 |
|------|------|------|
| `Authorization` | 是 | `Bearer {{YOUR_API_KEY}}` |
| `Content-Type` | 是 | `application/json` |

### Body 参数

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型名，传 `happyhorse-1.0-i2v` |
| `input` | object | 是 | 输入信息 |
| `input.prompt` | string | 否 | 提示词，**≤5000 个非中文字符** 或 **≤2500 个中文字符**，超出截断 |
| `input.media` | array[object] | 是 | 媒体列表（首帧图） |
| `input.media[].type` | string | 是 | 类型，固定 `first_frame` |
| `input.media[].url` | string | 是 | 媒体 URL 或 **裸 base64** |
| `parameters` | object | 否 | 生成参数 |
| `parameters.resolution` | string | 否 | `720P` 或 `1080P`（**默认**） |
| `parameters.duration` | integer | 否 | **[3, 15] 整数**，**默认 5** |
| `parameters.watermark` | boolean | 否 | 加水印 "Happy Horse" 右下角，**默认 `true`** |

### 请求示例

```json
{
  "model": "happyhorse-1.0-i2v",
  "input": {
    "prompt": "一只猫在草地上奔跑",
    "media": [
      {
        "type": "first_frame",
        "url": "/9j/4AAQSkZJRgABAQAAAQABAAD...（裸 base64）"
      }
    ]
  },
  "parameters": {
    "resolution": "720P",
    "duration": 5
  }
}
```

## 查询任务状态

**Endpoint**：`GET /alibailian/api/v1/tasks/{task_id}`

**响应**：

```json
{
  "request_id": "...",
  "output": {
    "task_id": "...",
    "task_status": "SUCCEEDED",
    "video_url": "https://...mp4"
  }
}
```

状态枚举：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`
视频 URL 路径：`output.video_url`

## 提交响应

```json
{
  "request_id": "1445f928-f1b6-9c43-8143-bfaddb5989cf",
  "output": {
    "task_id": "86438901-c911-4bfa-9137-621478c85efd",
    "task_status": "PENDING"
  }
}
```

`task_id` 在 `output.task_id`。`task_status` 初始为 `PENDING`。视频 URL 不在 submit 响应中，从 query 响应取。
