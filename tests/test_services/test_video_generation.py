"""VideoGenerationService 单元测试。

mock 掉 DingTalkClient 与 VideoGenerator，校验 9 步流程：
- 提示词 / 视频模型 / 首帧图 三必填校验
- 上传附件使用 video/mp4
- 字段写入 result_video_field（不是 result_image_field）
- 失败回写到 result_status_field
"""

from unittest.mock import AsyncMock

import pytest

from services.video_generation import VideoGenerationService


@pytest.fixture
def mock_dingtalk():
    return AsyncMock()


@pytest.fixture
def mock_video_generator():
    return AsyncMock()


@pytest.fixture
def service(mock_settings, mock_dingtalk, mock_video_generator):
    return VideoGenerationService(
        dingtalk=mock_dingtalk,
        video_generator=mock_video_generator,
        settings=mock_settings,
    )


def _ok_record(**overrides):
    """构造一个有效的视频记录字段字典。"""
    fields = {
        "提示词": "宇航员站起身走了",
        "视频模型": "kling-v2-5-turbo",
        "首帧图": [{"url": "/file/first.jpg", "filename": "first.jpg"}],
    }
    fields.update(overrides)
    return {"id": "rec_v001", "fields": fields}


# ─────────── 成功流程 ───────────


@pytest.mark.asyncio
async def test_process_success(service, mock_dingtalk, mock_video_generator):
    """完整 9 步成功流程。"""
    mock_dingtalk.get_record.return_value = _ok_record()
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_dingtalk.upload_attachment.return_value = {
        "filename": "generated_rec_v001.mp4",
        "size": 5678,
        "type": "video/mp4",
        "url": "/resource/v.mp4",
        "resourceId": "res_v001",
    }
    mock_video_generator.generate.return_value = b"FAKE_MP4_BYTES"

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    # 1-4 步校验
    mock_dingtalk.get_record.assert_awaited_once()
    table_config = mock_dingtalk.get_record.call_args[0][0]
    assert table_config.key == "zhuozhi-video"
    mock_dingtalk.download_file.assert_awaited_once_with("/file/first.jpg")

    # 5-7 步校验：调用 video_generator.generate，model 直接透传
    mock_video_generator.generate.assert_awaited_once_with(
        model="kling-v2-5-turbo",
        prompt="宇航员站起身走了",
        reference_image=b"fake_image_bytes",
        table_config=table_config,
    )

    # 8 步校验：upload_attachment 传 video/mp4
    mock_dingtalk.upload_attachment.assert_awaited_once()
    upload_args = mock_dingtalk.upload_attachment.call_args
    assert upload_args[0][0] is table_config
    assert upload_args[0][1] == b"FAKE_MP4_BYTES"
    assert upload_args[0][2] == "generated_rec_v001.mp4"
    assert upload_args[1]["media_type"] == "video/mp4"

    # 9 步校验：回写字段使用 result_video_field（"生成视频"）
    mock_dingtalk.update_record.assert_awaited_once()
    write_fields = mock_dingtalk.update_record.call_args[0][2]
    assert write_fields["生成结果"] == "成功"
    assert write_fields["生成视频"] == [
        {
            "filename": "generated_rec_v001.mp4",
            "size": 5678,
            "type": "video/mp4",
            "url": "/resource/v.mp4",
            "resourceId": "res_v001",
        }
    ]
    assert "生成时间" in write_fields


# ─────────── 校验失败 ───────────


@pytest.mark.asyncio
async def test_process_missing_prompt(service, mock_dingtalk, mock_video_generator):
    """提示词为空时回写失败，且不调用 generator。"""
    mock_dingtalk.get_record.return_value = _ok_record(提示词="")

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "失败: 提示词不能为空"
    mock_video_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_missing_video_model(service, mock_dingtalk, mock_video_generator):
    """视频模型为空时回写失败。"""
    mock_dingtalk.get_record.return_value = _ok_record(视频模型="")

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "失败: 视频模型不能为空"
    mock_video_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_missing_reference_image(service, mock_dingtalk, mock_video_generator):
    """首帧图为空时回写失败。"""
    mock_dingtalk.get_record.return_value = _ok_record(首帧图=None)

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert fields["生成结果"] == "失败: 首帧图不能为空"
    mock_video_generator.generate.assert_not_awaited()


# ─────────── 生成失败 ───────────


@pytest.mark.asyncio
async def test_process_video_generation_failure(service, mock_dingtalk, mock_video_generator):
    """视频生成抛异常时回写失败。"""
    mock_dingtalk.get_record.return_value = _ok_record()
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_video_generator.generate.side_effect = TimeoutError("video timeout")

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "失败" in fields["生成结果"]
    assert "[调用视频生成]" in fields["生成结果"]


@pytest.mark.asyncio
async def test_process_upload_failure(service, mock_dingtalk, mock_video_generator):
    """上传钉钉失败时回写失败信息。"""
    mock_dingtalk.get_record.return_value = _ok_record()
    mock_dingtalk.download_file.return_value = b"fake_image_bytes"
    mock_video_generator.generate.return_value = b"FAKE_MP4_BYTES"
    mock_dingtalk.upload_attachment.side_effect = RuntimeError("upload failed")

    await service.process(record_id="rec_v001", table_key="zhuozhi-video")

    mock_dingtalk.update_record.assert_awaited_once()
    fields = mock_dingtalk.update_record.call_args[0][2]
    assert "[上传视频到钉钉]" in fields["生成结果"]
