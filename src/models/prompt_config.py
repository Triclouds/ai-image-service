"""提示词配置数据模型 + 批量生图的 prompt 构建辅助。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 从扰动文本开头提取编号：支持 "1. xxx" / "1.xxx" / "12. xxx" / 前导空白
# 点后有无空格都匹配；不匹配（如无编号、顿号、冒号等）返回 None
_PERT_NUM_RE = re.compile(r"^\s*(\d+)\s*\.")
# 用于从扰动文本剥离编号（含点后空格），结果更干净
_PERT_STRIP_RE = re.compile(r"^\s*\d+\.\s*")
# 无编号扰动的文件名后缀起始值（与正常编号 1-99 区分）
_FALLBACK_SUFFIX_START = 101


def _extract_pert_number(pert: str) -> int | None:
    """从扰动文本开头提取编号。

    Examples:
        >>> _extract_pert_number("1. 模特站立")
        1
        >>> _extract_pert_number("12.xxx")
        12
        >>> _extract_pert_number("无编号")
        None
    """
    m = _PERT_NUM_RE.match(pert)
    return int(m.group(1)) if m else None


def _strip_pert_number(pert: str) -> str:
    """去掉扰动文本开头的编号前缀（含点后空格），不影响编号提取逻辑。

    Examples:
        >>> _strip_pert_number("1. - 上半身中景")
        '- 上半身中景'
        >>> _strip_pert_number("12.xxx")
        'xxx'
        >>> _strip_pert_number("无编号")
        '无编号'
    """
    return _PERT_STRIP_RE.sub("", pert, count=1)


def _to_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_text(value: Any) -> str:
    """把钉钉单元格的多种形态归一为字符串。

    支持：
    - None → ""
    - str / int / 其他标量 → str(value)
    - dict（钉钉单选 / 富文本块 {"name"|"text": ...}）→ 取 name 或 text
    - list（以上任意形态混合）→ 逐项递归拼接
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "")
    if isinstance(value, list):
        return "".join(_to_text(item) for item in value)
    return str(value)


def _split_perturbations(value: Any) -> list[str]:
    text = _to_text(value)
    return [p.strip() for p in text.split("\n") if p.strip()]


@dataclass
class PromptConfig:
    """从提示词表 fields 解析出的提示词配置。"""

    prompt: str
    count: int = 1
    perturbations: list[str] = field(default_factory=list)
    generate_type: str = ""
    aspect_ratio: str = ""
    resolution: str = ""

    @classmethod
    def from_prompt_record(
        cls, fields: dict[str, Any], prompt_table: PromptTableConfig
    ) -> PromptConfig:
        return cls(
            prompt=_to_text(fields.get(prompt_table.prompt_field)),
            generate_type=_to_text(fields.get(prompt_table.generate_type_field)),
            count=_to_int(fields.get(prompt_table.count_field), 1),
            aspect_ratio=_to_text(fields.get(prompt_table.aspect_ratio_field)),
            resolution=_to_text(fields.get(prompt_table.resolution_field)),
            perturbations=_split_perturbations(
                fields.get(prompt_table.perturbations_field)
            ),
        )

    @property
    def effective_count(self) -> int:
        return max(1, self.count)


def build_prompts(prompt_cfg: PromptConfig) -> list[str]:
    """根据 PromptConfig 构建完整 prompt 列表。

    扰动按索引对齐；不足时循环复用；count<=0 时按 1 处理。
    拼接前会去掉扰动文本开头的编号前缀（如 "1. - xxx" → "- xxx"），
    编号仅用于元数据（文件名后缀 / 同事对照），不进 AI 输入。
    """
    base = prompt_cfg.prompt
    count = prompt_cfg.effective_count
    perts = prompt_cfg.perturbations

    out: list[str] = []
    for i in range(count):
        if perts:
            pert = perts[i % len(perts)]
            out.append(f"{base}\n{_strip_pert_number(pert)}")
        else:
            out.append(base)
    return out