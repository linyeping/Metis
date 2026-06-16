"""Expert Tool Pattern — 专家工具模式。

将"子智能体"实现为一种特殊工具：封装专门的系统提示和工具子集，
在有限步数内完成某一类专门任务。

核心约束：
- 子智能体不能再调子智能体（防递归 token 爆炸）
- 每类专家有独立 ``max_turns``，桌面任务需要更多 observe/act/verify 轮次
- 使用与主循环相同的 LLM 配置

预定义专家：
1. **代码分析专家** — 项目分析、代码审查
2. **桌面操作专家** — 复杂桌面自动化任务
3. **Shell 专家** — 环境配置、构建、测试诊断
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_DEFAULT_EXPERT_MAX_TURNS = 5


# ---------------------------------------------------------------------------
# 专家定义
# ---------------------------------------------------------------------------

_EXPERT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "code_analysis_expert": {
        "description": "Delegate a code analysis task (project overview, code review, "
                       "dependency analysis) to a focused expert that only has read and search tools.",
        "system_prompt": (
            "You are a code analysis expert. Your job is to analyze code structure, "
            "find patterns, review code quality, and provide architectural insights.\n\n"
            "Rules:\n"
            "- Only use read and search tools — never modify files.\n"
            "- Be concise and structured in your analysis.\n"
            "- Focus on the specific question asked.\n"
            "- When done, provide a clear summary of findings.\n"
        ),
        "tool_whitelist": {
            "read_file", "read_multiple_files",
            "grep_search", "glob_search", "semantic_search",
            "generate_repo_map", "list_directory",
        },
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The analysis task to perform, e.g. 'Analyze the authentication module architecture'",
                },
            },
            "required": ["goal"],
        },
    },
    "desktop_expert": {
        "description": "Delegate a desktop automation task (window management, UI interaction) "
                       "to a focused expert with desktop-specific tools and SKILL.md knowledge.",
        "system_prompt": (
            "You are a desktop automation expert. Your job is to interact with "
            "desktop windows and applications using the available tools.\n\n"
            "Rules:\n"
            "- Use the Window2-style tools first for desktop apps: "
            "desktop_win2_status -> desktop_win2_observe -> desktop_win2_action, "
            "or desktop_win2_task for an end-to-end observe-plan-act-verify loop. "
            "Window2 coordinates are relative to the captured target window, not "
            "the full desktop.\n"
            "- After every meaningful action, observe the target window again and "
            "verify whether the goal advanced before continuing.\n"
            "- For any multi-step GUI task (open app, search, navigate, fill a form), "
            "prefer desktop_win2_task. Fall back to desktop_vision_task only when "
            "Window2 cannot resolve/capture the target window or the UI is a game/canvas "
            "that needs full-screen vision.\n"
            "- When you do use desktop_action, take coordinates directly from the "
            "screenshot you were shown; they are auto-mapped to the real screen.\n"
            "- Always list windows before acting on them.\n"
            "- Capture the target window to verify state before and after actions.\n"
            "- Report clear success/failure status.\n"
        ),
        "tool_whitelist": {
            "desktop_win2_status", "desktop_win2_observe", "desktop_win2_action",
            "desktop_win2_task",
            "desktop_screenshot", "desktop_action", "desktop_vision_task",
            "desktop_inventory", "desktop_window_list",
            "desktop_window_capture", "desktop_window_action",
        },
        "max_turns": 24,
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The desktop task to perform, e.g. 'Open Notepad and type Hello World'",
                },
            },
            "required": ["goal"],
        },
    },
    "shell_expert": {
        "description": "Delegate a shell/terminal task (environment setup, build, test diagnosis, "
                       "package management) to a focused expert.",
        "system_prompt": (
            "You are a shell and DevOps expert. Your job is to run commands, "
            "diagnose build/test failures, and configure environments.\n\n"
            "Rules:\n"
            "- Always check exit codes.\n"
            "- Combine commands with && where possible.\n"
            "- When a command fails, diagnose the error before retrying.\n"
            "- Read relevant config files to understand the project setup.\n"
        ),
        "tool_whitelist": {
            "execute_bash_command", "run_tests",
            "read_file", "list_directory",
            "check_dev_environment", "install_dev_runtime",
        },
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The shell task to perform, e.g. 'Run pytest and fix any failing tests'",
                },
            },
            "required": ["goal"],
        },
    },
}


# ---------------------------------------------------------------------------
# 专家执行器
# ---------------------------------------------------------------------------

def _create_expert_executor(
    expert_name: str,
    system_prompt: str,
    tool_whitelist: set[str],
    max_turns: int = _DEFAULT_EXPERT_MAX_TURNS,
) -> Callable[..., str]:
    """创建一个专家工具的执行函数。"""

    def expert_execute(goal: str = "", **kwargs: Any) -> str:
        """执行专家任务。"""
        from .agent_loop import run, AgentConfig, ContentEvent, ErrorEvent, DoneEvent
        from .tool_registry import get_registry

        registry = get_registry(include_mcp=False)

        # 构建限制工具集的配置
        config = AgentConfig(
            system_prompt=system_prompt,
            max_turns=max_turns,
            execution_mode="execute",
            llm_backend=_get_current_backend(),
            # FABLEADV-22: 子智能体必须继承主聊天的 base_url/api_key，否则
            # openai 兼容 provider（如 custom-openai / relay）会因缺 base_url 直接失败，
            # 逼主智能体退回最费步数的"截图+裸点击"循环。
            llm_base_url=_get_current_base_url(),
            llm_api_key=_get_current_api_key(),
            llm_model=_get_current_model(),
            enabled_tools=sorted(tool_whitelist),
        )

        messages = [{"role": "user", "content": goal or kwargs.get("task", "No goal specified")}]

        # 收集输出
        output_parts: list[str] = []
        try:
            for event in run(messages, config, registry=registry):
                if isinstance(event, ContentEvent) and event.text:
                    output_parts.append(event.text)
                elif isinstance(event, ErrorEvent):
                    output_parts.append(f"[Expert error: {event.message}]")
                elif isinstance(event, DoneEvent):
                    break
        except Exception as exc:
            logger.warning("Expert %s failed: %s", expert_name, exc)
            return f"[Expert {expert_name} failed: {exc}]"

        result = "\n".join(output_parts)
        if not result.strip():
            return f"[Expert {expert_name} completed but produced no output]"

        # 截断到合理长度
        if len(result) > 8_000:
            result = result[:7_500] + f"\n\n[... expert output truncated, {len(result) - 7500} chars omitted ...]"

        return f"[Expert: {expert_name}]\n{result}"

    return expert_execute


def _get_current_backend() -> str:
    """获取当前 LLM 后端配置。"""
    import os
    return os.environ.get("METIS_LLM_BACKEND", "deepseek")


def _get_current_model() -> str:
    """获取当前 LLM 模型配置。"""
    import os
    return os.environ.get("METIS_LLM_MODEL", "deepseek-chat")


def _get_current_base_url() -> str:
    """获取当前 LLM base_url（与主聊天同源的运行时 env，由 build_agent_config 写入）。"""
    import os
    for key in ("METIS_LLM_BASE_URL", "MIRO_LLM_BASE_URL", "DEEPSEEK_BASE_URL", "OPENAI_BASE_URL"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _get_current_api_key() -> str:
    """获取当前 LLM api_key（与主聊天同源的运行时 env）。"""
    import os
    for key in ("METIS_LLM_API_KEY", "MIRO_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


# ---------------------------------------------------------------------------
# 注册接口
# ---------------------------------------------------------------------------

def register_expert_tools(registry: Any) -> int:
    """将所有预定义专家注册为工具。返回注册数量。

    Parameters
    ----------
    registry:
        ToolRegistry 实例。

    Returns
    -------
    int
        成功注册的专家工具数量。
    """
    from .tool_registry import ToolDefinition

    count = 0
    for expert_name, definition in _EXPERT_DEFINITIONS.items():
        tool_whitelist = definition["tool_whitelist"]
        system_prompt = definition["system_prompt"]
        max_turns = int(definition.get("max_turns") or _DEFAULT_EXPERT_MAX_TURNS)

        executor = _create_expert_executor(expert_name, system_prompt, tool_whitelist, max_turns=max_turns)

        try:
            registry.register(
                ToolDefinition(
                    name=expert_name,
                    description=definition["description"],
                    parameters=definition["parameters"],
                    execute_fn=executor,
                    usage_hint=f"Use this to delegate {expert_name.replace('_', ' ')} tasks.",
                    source="expert",
                    toolset="expert",
                )
            )
            count += 1
        except Exception as exc:
            logger.warning("Failed to register expert tool %s: %s", expert_name, exc)

    return count


def get_expert_names() -> List[str]:
    """返回所有可用的专家工具名。"""
    return list(_EXPERT_DEFINITIONS.keys())
