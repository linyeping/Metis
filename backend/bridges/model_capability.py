from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCapabilities:
    tier: int
    effective_context: int
    supports_tool_calling: bool
    supports_structured_output: bool
    instruction_adherence: str
    detected_family: str
    detected_model: str
    detection_method: str
    vision_protocol: str = "legacy"
    detected_at: float = field(default_factory=time.time)


_TIER_PATTERNS: list[tuple[str, int, str]] = [
    (r"claude.*opus|claude-4(?!.*haiku|.*sonnet)", 1, "claude"),
    (r"gpt-4\.5|gpt-5|o[1-9](?:$|[-_])", 1, "openai"),
    (r"gemini.*ultra|gemini-2\.0-pro", 1, "gemini"),
    (r"claude.*sonnet|claude-4.*sonnet", 2, "claude"),
    (r"gpt-4o(?!-mini)|gpt-4-turbo", 2, "openai"),
    (r"gemini.*pro|gemini-2\.0-flash", 2, "gemini"),
    (r"deepseek.*v4|deepseek.*chat", 2, "deepseek"),
    (r"qwen3-coder-plus|qwen3-max", 2, "qwen"),
    (r"kimi-k2", 2, "moonshot"),
    (r"claude.*haiku", 3, "claude"),
    (r"gpt-4o-mini|gpt-3\.5", 3, "openai"),
    (r"gemini.*flash(?!.*pro)", 3, "gemini"),
    (r"deepseek.*lite|deepseek.*coder", 3, "deepseek"),
    (r"qwen.*turbo|qwen.*lite", 3, "qwen"),
]


# 家族特化执行纪律。目前只有 DeepSeek：eval 实测它在紧预算/多工具任务里最常见的
# 失败 = 把轮次耗在探索（狂读文件、刷 repo_map、空转 runtime_job）上，迟迟不产出
# 交付物。这块在 family==deepseek 时追加到系统提示里（marker 去重），不动其它家族。
DEEPSEEK_EFFICIENCY_PROMPT = """---
[DeepSeek 执行纪律]
你在紧预算/多工具任务里最常见的失败模式 = 把轮次耗在探索上、迟迟不产出交付物。务必：
1. 任务要求产出文件/答案（如写入 answer.md、创建某文件）时，**尽早写出第一版**，哪怕粗糙，之后再迭代完善；不要等"完全探明"才动手。宁可先交付不完整结果，也不要空手耗尽轮次。
2. 不要逐个 read_file 去摸清项目结构。先看上下文里已有的 repo map（或只 generate_repo_map 一次），再用 grep_search 定位符号，只在确需看真实实现时才 read_file。
3. metis_runtime_job / 后台任务只在确需执行代码时用，不要反复起任务空转。
4. 每完成一步就推进下一步，不要反复重写待办（todo_write）却不动手。
"""

DEEPSEEK_EFFICIENCY_MARKER = "[DeepSeek 执行纪律]"


def family_prompt_for_model(model_name: str) -> str:
    """按模型家族返回特化执行纪律块。无匹配返回空串。"""
    family = detect_from_model_name(model_name).detected_family
    if family == "deepseek":
        return DEEPSEEK_EFFICIENCY_PROMPT
    return ""


def tier_compact_thresholds(tier: int) -> tuple[float, float, float]:
    if tier == 1:
        return (0.65, 0.82, 0.93)
    if tier == 3:
        return (0.50, 0.70, 0.85)
    return (0.60, 0.80, 0.92)


def detect_from_model_name(model_name: str) -> ModelCapabilities:
    name_lower = str(model_name or "").strip().lower()
    vision_protocol = _vision_protocol_for_model(name_lower)

    for pattern, tier, family in _TIER_PATTERNS:
        if re.search(pattern, name_lower):
            return ModelCapabilities(
                tier=tier,
                effective_context=_context_for_model(name_lower),
                supports_tool_calling=True,
                supports_structured_output=(tier <= 2),
                instruction_adherence=("high" if tier == 1 else "medium" if tier == 2 else "low"),
                detected_family=family,
                detected_model=model_name,
                detection_method="name_match",
                vision_protocol=vision_protocol,
            )

    return ModelCapabilities(
        tier=2,
        effective_context=_context_for_model(name_lower),
        supports_tool_calling=True,
        supports_structured_output=False,
        instruction_adherence="medium",
        detected_family="unknown",
        detected_model=model_name,
        detection_method="default",
        vision_protocol=vision_protocol,
    )


def _vision_protocol_for_model(name: str) -> str:
    if name.startswith(("gpt-5.4", "gpt-5.5", "computer-use-preview")):
        return "openai_cua"
    if name.startswith(("claude-sonnet-4", "claude-opus-4", "claude-4")):
        return "anthropic_cua"
    if name.startswith(("deepseek", "kimi", "glm")):
        return "none"
    if name.startswith(("gpt-4o", "gpt-4.1", "gemini", "qwen-vl")) or "vl" in name:
        return "legacy"
    return "legacy"


def _context_for_model(name: str) -> int:
    from backend.web.llm_state import context_limit

    return context_limit(name)
