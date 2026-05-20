# SDK 实现指南

> 占位文档 — 待补充

## 素材图下载

- 使用 `httpx.AsyncClient.get()` 发起 GET 请求
- 请求头携带 `X-Access-Token: {access_token}`
- 响应体为原始图片二进制数据（`bytes`）

## 附件上传

见 [上传附件.md](./上传附件.md)
