# 海螺（Hailuo）视频生成 — 图生视频 API

> 来源：https://vectorengine.apifox.cn/api-373137982
> 中转域名：`https://api.vectorengine.ai`
> Model: `MiniMax-Hailuo-2.3`（示例所用）

## 提交视频生成任务

**Endpoint**：`POST /minimax/v1/video_generation`

### Header 参数

| 字段 | 必需 | 示例 |
|------|------|------|
| `Content-Type` | 是 | `application/json` |
| `Accept` | 是 | `application/json` |
| `Authorization` | 是 | `Bearer {{YOUR_API_KEY}}` |

### Body 参数

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 模型名，传 `MiniMax-Hailuo-2.3` |
| `prompt` | string | 是 | 提示词 |
| `duration` | integer | 是 | 视频时长，**支持 6 或 10**（非 5/10） |
| `first_frame_image` | string | 是 | 首帧图，**裸 base64** 或 URL |
| `resolution` | string | 否 | 默认 `768P` |
| `prompt_optimizer` | boolean | 否 | 提示词优化，默认 `true` |

### 请求示例

```json
{
  "model": "MiniMax-Hailuo-2.3",
  "prompt": "一只小猪在高速公路上快乐的奔跑",
  "duration": 10,
  "first_frame_image": "/9j/4AAQSkZJRgABAQAAAQABAAD...（裸 base64）",
  "resolution": "768P",
  "prompt_optimizer": true
}
```

## 查询任务状态

**Endpoint**：`GET /minimax/v1/query/video_generation?task_id={id}`

**响应**：

```json
{
  "task_id": "...",
  "base_resp": {
    "status_code": 0,
    "status_msg": "success"
  },
  "data": {
    "task_id": "...",
    "status": "Success",
    "file": {
      "download_url": "https://...mp4"
    }
  }
}
```

状态枚举：`Queueing` / `Processing` / `Success` / `Fail`
视频 URL 路径：`data.file.download_url`

## 提交响应

```json
{
  "task_id": "306792606023824",
  "base_resp": {
    "status_code": 0,
    "status_msg": "success"
  }
}
```

`task_id` 在**顶层**。`base_resp.status_code == 0` 表示成功。
