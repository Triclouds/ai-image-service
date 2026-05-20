"""AI 生图引擎。

按 model 分派到对应 SDK（Google / OpenAI）。
"""

from generator.engine import AIGenerator

__all__ = ["AIGenerator"]
