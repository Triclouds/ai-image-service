"""API 路由端到端测试。"""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    """健康检查接口。"""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_generate_success(client):
    """正常触发生图 — 202 Accepted。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": "rec_001"},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["record_id"] == "rec_001"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_generate_with_table_key(client):
    """携带 table_key 触发生图。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": "rec_001", "table_key": "clothing"},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_generate_missing_record_id(client):
    """缺少 record_id 返回 422（Pydantic 校验失败）。"""
    response = await client.post(
        "/api/v1/generate",
        json={},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_empty_record_id(client):
    """record_id 为空字符串应返回 422（Pydantic 校验失败）。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": ""},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generate_no_auth(client):
    """未携带 Authorization header 返回 401。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": "rec_001"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Missing API key"


@pytest.mark.asyncio
async def test_generate_wrong_auth(client):
    """错误的 API Key 返回 401。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": "rec_001"},
        headers={"Authorization": "Bearer wrong_key"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_generate_wrong_auth_format(client):
    """非 Bearer 格式的 Authorization 返回 401。"""
    response = await client.post(
        "/api/v1/generate",
        json={"record_id": "rec_001"},
        headers={"Authorization": "Basic dGVzdA=="},
    )
    assert response.status_code == 401


# ─────────── 视频生成接口 ───────────


@pytest.mark.asyncio
async def test_video_generate_success(client):
    """正常触发视频生成 — 202 Accepted。"""
    response = await client.post(
        "/api/v1/video/generate",
        json={"record_id": "rec_v001", "table_key": "zhuozhi-video"},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["record_id"] == "rec_v001"


@pytest.mark.asyncio
async def test_video_generate_missing_table_key(client):
    """缺少 table_key 返回 422（Pydantic 校验失败）。"""
    response = await client.post(
        "/api/v1/video/generate",
        json={"record_id": "rec_v001"},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_video_generate_empty_record_id(client):
    """record_id 为空字符串应返回 422。"""
    response = await client.post(
        "/api/v1/video/generate",
        json={"record_id": "", "table_key": "zhuozhi-video"},
        headers={"Authorization": "Bearer test_api_key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_video_generate_no_auth(client):
    """未携带 Authorization header 返回 401。"""
    response = await client.post(
        "/api/v1/video/generate",
        json={"record_id": "rec_v001", "table_key": "zhuozhi-video"},
    )
    assert response.status_code == 401
