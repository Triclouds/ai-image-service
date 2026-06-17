"""AI 生成引擎。

按 model 分派到对应 SDK（Google / OpenAI 生图；Kling / Hailuo / Wanxiang 生视频）。
"""

from generator.engine import AIGenerator
from generator.video_engine import VideoGenerator, _resolve_provider

__all__ = ["AIGenerator", "VideoGenerator", "_resolve_provider"]
