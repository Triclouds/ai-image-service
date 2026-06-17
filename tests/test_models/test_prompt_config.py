"""PromptConfig 与 build_prompts 单元测试。"""

from config import PromptTableConfig
from models.prompt_config import PromptConfig, build_prompts


def _prompt_table(**overrides) -> PromptTableConfig:
    defaults = dict(
        prompt_field="提示词",
        generate_type_field="生成类型",
        count_field="生成数量",
        aspect_ratio_field="生成比例",
        resolution_field="分辨率",
        perturbations_field="扰动列表",
    )
    defaults.update(overrides)
    return PromptTableConfig(**defaults)


def test_build_prompts_standard_scenario():
    """count=3, perts=[a,b,c] → 3 个 prompt，扰动按索引对齐。"""
    cfg = PromptConfig(prompt="base", count=3, perturbations=["a", "b", "c"])
    prompts = build_prompts(cfg)
    assert len(prompts) == 3
    assert prompts[0] == "base\na"
    assert prompts[1] == "base\nb"
    assert prompts[2] == "base\nc"


def test_build_prompts_perturbations_insufficient():
    """count=3, perts=[a] → 3 个 prompt，全部用 a（循环复用）。"""
    cfg = PromptConfig(prompt="base", count=3, perturbations=["a"])
    prompts = build_prompts(cfg)
    assert len(prompts) == 3
    assert prompts[0] == "base\na"
    assert prompts[1] == "base\na"
    assert prompts[2] == "base\na"


def test_build_prompts_no_perturbations():
    """count=3, perts=[] → 3 个 prompt，全部是 base。"""
    cfg = PromptConfig(prompt="base", count=3, perturbations=[])
    prompts = build_prompts(cfg)
    assert len(prompts) == 3
    assert prompts == ["base", "base", "base"]


def test_effective_count_zero():
    """count=0 → effective_count=1。"""
    cfg = PromptConfig(prompt="base", count=0)
    assert cfg.effective_count == 1
    assert len(build_prompts(cfg)) == 1


def test_effective_count_none():
    """count=None → effective_count=1（由 from_prompt_record 默认值保证）。"""
    cfg = PromptConfig(prompt="base")
    assert cfg.count == 1
    assert cfg.effective_count == 1


def test_from_prompt_record_invalid_count_strings():
    """count 字段异常值（"" / "invalid"）→ 默认 1。"""
    pt = _prompt_table()
    cfg = PromptConfig.from_prompt_record({"生成数量": ""}, pt)
    assert cfg.count == 1
    cfg = PromptConfig.from_prompt_record({"生成数量": "invalid"}, pt)
    assert cfg.count == 1


def test_from_prompt_record_rich_text_field():
    """钉钉富文本字段 [{text: "..."}, ...] → 正确解析。"""
    pt = _prompt_table()
    cfg = PromptConfig.from_prompt_record(
        {"提示词": [{"text": "hello "}, {"text": "world"}]}, pt
    )
    assert cfg.prompt == "hello world"


def test_from_prompt_record_perturbations_with_blank_lines():
    """扰动文本带空行 → 跳过空行。"""
    pt = _prompt_table()
    cfg = PromptConfig.from_prompt_record(
        {"扰动列表": "a\n\nb\n"}, pt
    )
    assert cfg.perturbations == ["a", "b"]


def test_from_prompt_record_full_fields():
    """完整 fields 解析。"""
    pt = _prompt_table()
    cfg = PromptConfig.from_prompt_record(
        {
            "提示词": "p",
            "生成类型": "t",
            "生成数量": 3,
            "生成比例": "16:9",
            "分辨率": "1080p",
            "扰动列表": "x\ny\nz",
        },
        pt,
    )
    assert cfg.prompt == "p"
    assert cfg.generate_type == "t"
    assert cfg.count == 3
    assert cfg.aspect_ratio == "16:9"
    assert cfg.resolution == "1080p"
    assert cfg.perturbations == ["x", "y", "z"]


def test_build_prompts_with_rich_text_prompt():
    """钉钉富文本 prompt 也能正确传到 build_prompts。"""
    pt = _prompt_table()
    cfg = PromptConfig.from_prompt_record(
        {"提示词": [{"text": "base"}], "生成数量": 2}, pt
    )
    assert build_prompts(cfg) == ["base", "base"]