# -*- coding: utf-8 -*-
"""自然语言理解 + 上下文编译。

参考 mine/miro 模式:
- intent_router.py → 意图分类（启发式 + 可选 LLM）
- prompt_runtime.py → 上下文层级编译
- mode_router.py → 模式感知的能力过滤
- context_builder.py → 最终消息列表装配

本模块将用户的自然语言指令解析为:
1. 意图类型（操作类、查询类、创建类、导航类等）
2. 操作上下文（当前环境、已有信息）
3. 最终注入 LLM 的结构化 prompt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any



# ─── 意图分类（参考 mine/miro intent_router 的关键词计数法，扩展为屏幕操作域）───

class ScreenIntent:
    NAVIGATE = "navigate"      # 打开/切换/进入某个地方
    INTERACT = "interact"      # 点击/输入/选择/勾选
    CREATE = "create"          # 新建文件/文档/项目
    SEARCH = "search"          # 搜索/查找
    CONFIGURE = "configure"    # 设置/修改配置
    TRANSFER = "transfer"      # 拖拽/复制/移动文件
    COMMUNICATE = "communicate"  # 发消息/邮件
    OBSERVE = "observe"        # 看一下/截图/告诉我
    MULTI_STEP = "multi_step"  # 复合任务


_INTENT_KEYWORDS: dict[str, list[str]] = {
    ScreenIntent.NAVIGATE: [
        "打开", "切换", "进入", "去", "跳转", "启动", "运行", "访问",
        "open", "go to", "switch", "navigate", "launch",
    ],
    ScreenIntent.INTERACT: [
        "点击", "点", "按", "输入", "填写", "选择", "勾选", "取消勾选", "确认", "提交",
        "click", "type", "select", "check", "press", "submit",
    ],
    ScreenIntent.CREATE: [
        "新建", "创建", "新增", "添加", "写",
        "create", "new", "add", "write",
    ],
    ScreenIntent.SEARCH: [
        "搜索", "查找", "找", "搜",
        "search", "find", "look for", "grep",
    ],
    ScreenIntent.CONFIGURE: [
        "设置", "配置", "修改", "调整", "改",
        "set", "config", "change", "modify", "adjust",
    ],
    ScreenIntent.TRANSFER: [
        "拖", "拖拽", "复制", "移动", "剪切", "粘贴", "上传", "下载",
        "drag", "copy", "move", "paste", "upload", "download",
    ],
    ScreenIntent.COMMUNICATE: [
        "发送", "发", "消息", "邮件", "聊天", "回复",
        "send", "message", "email", "chat", "reply",
    ],
    ScreenIntent.OBSERVE: [
        "看看", "截图", "告诉我", "什么", "哪里", "显示",
        "show", "tell me", "what", "where", "screenshot",
    ],
}


def classify_intent(user_message: str) -> str:
    """对用户指令做意图分类。"""
    msg = user_message.lower()
    scores: dict[str, int] = {}

    for intent, keywords in _INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg)
        if score > 0:
            scores[intent] = score

    if not scores:
        return ScreenIntent.MULTI_STEP

    if len(scores) >= 3:
        return ScreenIntent.MULTI_STEP

    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ─── 上下文编译（参考 mine/miro prompt_runtime.compile_prompt_runtime）───

@dataclass
class ScreenContext:
    """编译后的屏幕操作上下文，注入给 LLM。"""
    intent: str
    goal: str
    environment_summary: str = ""
    action_history_summary: str = ""
    constraints: str = ""
    extra: str = ""


def compile_context(
    user_message: str,
    env_info: dict[str, Any] | None = None,
    action_history: list[dict] | None = None,
) -> ScreenContext:
    """编译用户指令 + 环境信息为结构化上下文。

    类似 mine/miro 的 compile_prompt_runtime:
    base → workspace → agent state → mode → workflow → open files → terminal
    这里简化为: goal → intent → env → history → constraints
    """
    intent = classify_intent(user_message)

    env_summary = ""
    if env_info:
        parts = []
        if env_info.get("os"):
            parts.append(f"操作系统: {env_info['os']}")
        if env_info.get("screen_size"):
            parts.append(f"屏幕分辨率: {env_info['screen_size']}")
        if env_info.get("active_window"):
            parts.append(f"当前活跃窗口: {env_info['active_window']}")
        if env_info.get("cursor_pos"):
            parts.append(f"鼠标位置: {env_info['cursor_pos']}")
        env_summary = "; ".join(parts)

    history_summary = ""
    if action_history:
        recent = action_history[-5:]
        lines = [f"  {i+1}. {h.get('action','?')}({h.get('params',{})}) → {h.get('reasoning','')}"
                 for i, h in enumerate(recent)]
        history_summary = "\n".join(lines)

    constraints = _intent_constraints(intent)

    return ScreenContext(
        intent=intent,
        goal=user_message,
        environment_summary=env_summary,
        action_history_summary=history_summary,
        constraints=constraints,
    )


def _intent_constraints(intent: str) -> str:
    """根据意图类型生成约束/提示。"""
    hints = {
        ScreenIntent.NAVIGATE: "优先使用快捷键（如 Win+E 打开资源管理器、Alt+Tab 切换窗口），比鼠标点击更快更准",
        ScreenIntent.INTERACT: "仔细看截图，确保点击的是正确的按钮/输入框；先点击目标使其获得焦点",
        ScreenIntent.CREATE: "先确认在正确的位置/应用中，再执行创建操作",
        ScreenIntent.SEARCH: "优先使用应用内搜索快捷键（Ctrl+F / Ctrl+K 等），不要手动滚动查找",
        ScreenIntent.CONFIGURE: "修改设置前先确认当前值，操作后验证是否生效",
        ScreenIntent.TRANSFER: "拖拽时确保源和目标都可见；大量文件优先用 Ctrl+C/V",
        ScreenIntent.COMMUNICATE: "发送前检查收件人和内容是否正确",
        ScreenIntent.OBSERVE: "只需截图并描述，不要执行任何操作",
        ScreenIntent.MULTI_STEP: "复杂任务分步执行，每一步做完验证再继续",
    }
    return hints.get(intent, "")


def build_extra_context(ctx: ScreenContext) -> str:
    """将 ScreenContext 转为注入 LLM 的额外上下文字符串。"""
    parts = []

    parts.append(f"意图类型: {ctx.intent}")

    if ctx.constraints:
        parts.append(f"操作提示: {ctx.constraints}")

    if ctx.environment_summary:
        parts.append(f"环境: {ctx.environment_summary}")

    if ctx.action_history_summary:
        parts.append(f"已执行:\n{ctx.action_history_summary}")

    if ctx.extra:
        parts.append(ctx.extra)

    return "\n".join(parts)
