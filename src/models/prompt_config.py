"""提示词配置数据模型 + 批量生图的 prompt 构建辅助。"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

# 从扰动文本开头提取编号：支持 "1. xxx" / "1.xxx" / "12. xxx" / "1、xxx" / 前导空白
# 点/顿号后有无空格都匹配；不匹配（如无编号、冒号等）返回 None
_PERT_NUM_RE = re.compile(r"^\s*(\d+)\s*[.,]\s*")
# 用于从扰动文本剥离编号（含点/顿号后空格），结果更干净
_PERT_STRIP_RE = re.compile(r"^\s*\d+\s*[.,、]\s*")
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


# ============================================================================
# 搜推素材三段式：长文本拆段 + 重组
# ============================================================================


# 段标题识别正则：行首"一、""二、"等（中文数字+顿号）。
# 用于快速定位段落起点。
_SECTION_HEADER_RE = re.compile(r"(?m)^[一二三四五六七八九十]+、.*$")


def parse_prompt_sections(
    prompt_text: str,
    section_titles: dict[str, str],
) -> dict[str, list[str]]:
    """把长文本按段标题切分为 {类型名: [子项, ...]}。

    段标题识别：用 section_titles 的 key（如 "一、"/"二、"/"三、"）作为段起点。
    下一段起点之前的所有行都属于上一段。
    段内的"1、xxx / 2、xxx"子项按换行切分，strip 后保留非空。

    Args:
        prompt_text: 提示词表"提示词"字段的长文本。
        section_titles: 段标题前缀 → 类型名映射，
            如 {"一、": "白底图", "二、": "场景图", "三、": "细节图"}。

    Returns:
        {"白底图": ["1、女装纯白底悬挂主图...", "2、...", ...], "场景图": [...], "细节图": [...]}
        未在 section_titles 中出现的段被丢弃。
    """
    if not prompt_text or not section_titles:
        return {}

    lines = prompt_text.splitlines()
    # 找出所有段标题的行号
    section_starts: list[tuple[int, str]] = []  # [(行号, 类型名), ...]
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        for prefix, type_name in section_titles.items():
            if line_stripped.startswith(prefix):
                section_starts.append((idx, type_name))
                break

    if not section_starts:
        return {}

    result: dict[str, list[str]] = {}
    for i, (start_line, type_name) in enumerate(section_starts):
        end_line = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(lines)
        body_lines = lines[start_line + 1:end_line]
        items = [s.strip() for s in body_lines if s.strip()]
        if items:
            result[type_name] = items

    return result


def build_sousuo_prompts(
    base_prompt: str,
    sections: dict[str, list[str]],
    output_order: list[str],
    count_per_section: int = 3,
    seed: int | str | None = None,
) -> list[tuple[str, str]]:
    """按 output_order 输出 (完整prompt, 段名) 对。

    每段用 `random.sample` 从 candidates 里随机抽 count_per_section 个子项
    （不足则取实际数量，**不补位**）。抽样后**按原顺序排序**，保证段内顺序稳定。
    seed 决定随机性：传 record_id 等可复现值时，同一 record 多次跑结果一致。

    每个 prompt 由 base_prompt + 子项文本拼接而成（去掉子项开头的数字编号）。
    段名（白底图/场景图/细节图）原样返回，调用方用于决定序号。

    Args:
        base_prompt: 基础提示词（一般为空或上下文总述）。
        sections: parse_prompt_sections 的输出，{类型名: [子项, ...]}。
        output_order: 输出顺序，如 ["细节图", "场景图", "白底图"]。
        count_per_section: 每段抽几张，默认 3。
        seed: 随机种子；None 时用 `random`（每次跑不同），传 int/str 时可复现。

    Returns:
        [(完整prompt, 段名), ...]，长度 ≤ len(output_order) * count_per_section。
    """
    rng = random.Random(seed)
    out: list[tuple[str, str]] = []
    for type_name in output_order:
        items = sections.get(type_name, [])
        if not items:
            continue
        n = min(count_per_section, len(items))
        sampled = rng.sample(items, n)
        # 按原顺序排序，保证 1-3 / 4-6 / 7-9 顺序稳定
        sampled.sort(key=items.index)
        for pert in sampled:
            if base_prompt:
                out.append((f"{base_prompt}\n{_strip_pert_number(pert)}", type_name))
            else:
                out.append((_strip_pert_number(pert), type_name))
    return out


def assign_sousuo_index(
    ordered_segments: list[tuple[str, str]],
    count_per_section: int = 3,
) -> list[tuple[str, str, int]]:
    """为已按 output_order 排好的段序列分配 1~N 序号。

    序号规则：从 1 开始连续递增，不为空位预留编号。
    count_per_section 仍按业务预期传入（用于日志/可读性），但不再参与序号计算。

    Args:
        ordered_segments: build_sousuo_prompts 的输出（已按 output_order 排好）。
        count_per_section: 每段几张，用于日志可读性。

    Returns:
        [(完整prompt, 段名, 序号), ...]，序号 1~N 连续。
    """
    return [
        (prompt, tname, idx + 1)
        for idx, (prompt, tname) in enumerate(ordered_segments)
    ]
