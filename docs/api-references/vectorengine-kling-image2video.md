# Kling 快手可灵 — 图生视频 API

> 来源：https://vectorengine.apifox.cn/api-446220715
> 中转域名：`https://api.vectorengine.ai`

## 提交视频生成任务

**Endpoint**：`POST /kling/v1/videos/image2video`

### Header 参数

| 字段 | 必需 | 示例 |
|------|------|------|
| `Content-Type` | 是 | `application/json` |
| `Accept` | 是 | `application/json` |
| `Authorization` | 是 | `Bearer {{YOUR_API_KEY}}` |

### Body 参数

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model_name` | string | 是 | 模型枚举：`kling-v1` / `kling-v1-5` / `kling-v1-6` / `kling-v2-5-turbo` |
| `image` | string | 是 | 参考图像，**裸 base64** 或图片 URL，jpg/jpeg/png，**≤10MB，≥300×300px** |
| `image_tail` | string | 否 | 尾帧图，格式同 `image`。**带尾帧时 duration 仅支持 5** |
| `prompt` | string | 否 | 不超过 5000 字符 |
| `negative_prompt` | string | 否 | 不超过 2000 字符 |
| `cfg_scale` | number | 否 | 0-1，值越大相关性越强 |
| `duration` | number | 是 | `5` 或 `10`（含尾帧时仅 5） |
| `callback_url` | string | 否 | 回调通知（本项目不用，走主动轮询） |
| `aspect_ratio` | string | 否 | 见示例值 `"1:1"` |

### 请求示例

```json
{
  "model_name": "kling-v2-5-turbo",
  "prompt": "宇航员站起身走了",
  "negative_prompt": "",
  "image_tail": "",
  "aspect_ratio": "1:1",
  "duration": "5",
  "image": "/9j/4AAQSkZJRgABAQAAAQABAAD...（裸 base64）"
}
```

## 查询任务状态

**Endpoint**：`GET /kling/v1/videos/image2video/{task_id}`

**响应**：

```json
{
  "code": 0,
  "message": "SUCCEED",
  "data": {
    "task_id": "...",
    "task_status": "succeed",
    "task_result": {
      "videos": [{"url": "https://...mp4"}]
    }
  }
}
```

状态枚举：`submitted` / `processing` / `succeed` / `failed`
视频 URL 路径：`data.task_result.videos[0].url`

## 提交响应

```json
{
  "code": 0,
  "message": "SUCCEED",
  "request_id": "...",
  "data": {
    "task_id": "...",
    "task_status": "submitted",
    "task_info": {},
    "created_at": <ms>,
    "updated_at": <ms>
  }
}
```
