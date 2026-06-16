# -*- coding: utf-8 -*-
"""屏幕理解：截图 → 多模态 LLM → 结构化动作。

支持多个后端（按优先级自动选择）:
1. 本地 Ollama（llava / bakllava）— 零成本
2. 阿里云百炼 Qwen-VL — 低成本
3. Google Gemini Vision — 免费额度
4. OpenAI GPT-4o / Claude — 强但贵

返回标准 Action 格式供 vision_loop 执行。
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from . import desk_log


# ─── 动作类型定义 ───

class ActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    KEY = "key"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    DRAG = "drag"
    MOVE = "move"
    WAIT = "wait"
    DONE = "done"
    FAIL = "fail"
    ASK_USER = "ask_user"


@dataclass
class ScreenAction:
    """LLM 返回的一个屏幕操作。"""
    action: ActionType
    params: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {"action": self.action.value, "params": self.params, "reasoning": self.reasoning}


# ─── 系统提示词（参考 mine/miro prompt_runtime + OpenClaw peekaboo 模式） ───

SYSTEM_PROMPT = """你是 Windows 电脑操作助手。你会收到屏幕截图和一组关于当前状态的具体问题。

## 回答格式

你必须按以下格式回答（先回答问题，再给操作）:

**观察**: 简述你在截图中看到了什么（2-3句话）
**回答**: 逐一回答提出的问题
**操作**: 一个 JSON
```json
{"action": "类型", "params": {参数}, "reasoning": "说明"}
```

## 操作类型

| action | params | 说明 |
|--------|--------|------|
| click | {"x": 数字, "y": 数字} | 左键单击 |
| double_click | {"x": 数字, "y": 数字} | 双击打开 |
| right_click | {"x": 数字, "y": 数字} | 右键菜单 |
| type | {"text": "文字"} | 键入文字（必须先 click 获取焦点！） |
| key | {"key": "键名"} | 按键 |
| hotkey | {"keys": ["ctrl", "c"]} | 组合键 |
| scroll | {"clicks": 数字, "x": 数字, "y": 数字} | 滚轮 |
| drag | {"start_x", "start_y", "end_x", "end_y"} | 拖拽 |
| wait | {"seconds": 数字} | 等待 |
| done | {} | 任务已完成 |
| fail | {"reason": "原因"} | 无法完成 |

## 关键规则

1. **坐标 = 像素坐标**，左上角是 (0,0)，图片的宽高就是屏幕分辨率（会在问题里告诉你）。
2. **type 之前必须先 click 输入区域获取焦点**，否则打字无效。
3. **桌面图标双击图标图片中心**（不是文字标签），任务栏单击。
4. 每次只返回**一个**操作。
"""


# ─── LLM 调用后端 ───

def _miro_root_path() -> Path:
    """orchestrator → desk_automation → Tools → miro。"""
    return Path(__file__).resolve().parents[3]


def _vision_cfg_for_log(vcfg: dict[str, Any]) -> dict[str, Any]:
    """调试日志用，避免打印密钥。"""
    redact = frozenset({"openai_api_key", "anthropic_api_key", "dashscope_api_key", "gemini_api_key"})
    out: dict[str, Any] = {}
    for k, v in vcfg.items():
        if k in redact and isinstance(v, str) and v.strip():
            out[k] = "***"
        else:
            out[k] = v
    return out


def _load_vision_config() -> dict[str, Any]:
    """视觉 API 仅来自 ``mine/miro/web/config.py``（见 ``get_vision_api_dict``）。"""
    import sys

    try:
        root = str(_miro_root_path())
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.web.config import get_vision_api_dict

        return dict(get_vision_api_dict())
    except Exception as e:
        try:
            desk_log.log("ERROR", "screen_reader", f"无法加载 web.config 视觉配置: {e}")
        except Exception:
            pass
        return {}


def _encode_image_base64(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode("ascii")


_OPENAI_CUA_MODEL_PREFIXES = ("gpt-5.4", "gpt-5.5", "computer-use-preview")
_ANTHROPIC_CUA_MODEL_PREFIXES = ("claude-sonnet-4", "claude-opus-4")


def _should_use_native_cua(model: str, backend_type: str = "") -> str | None:
    """Return the native computer-use protocol for a model, if one is known."""
    model_lower = str(model or "").strip().lower()
    backend_lower = str(backend_type or "").strip().lower()
    if model_lower.startswith(_OPENAI_CUA_MODEL_PREFIXES):
        return "openai_cua"
    if model_lower.startswith(_ANTHROPIC_CUA_MODEL_PREFIXES):
        return "anthropic_cua"
    if backend_lower == "anthropic" and model_lower.startswith(("claude-", "claude_")):
        return "anthropic_cua"
    return None


def _is_official_cua_endpoint(protocol: str, base_url: str) -> bool:
    """Native Computer-Use APIs (OpenAI responses/computer_call, Anthropic
    computer-use) only exist on the vendors' official endpoints. Relays and
    OpenAI-compatible gateways expose Chat Completions only, so native CUA
    requests against them fail — fall back to legacy vision there."""
    url = str(base_url or "").strip().lower()
    if not url:
        # No explicit base_url → vendor SDK default (official). Keep native.
        return True
    if protocol == "openai_cua":
        return "api.openai.com" in url
    if protocol == "anthropic_cua":
        return "api.anthropic.com" in url
    return False


def _native_cua_protocol(vcfg: dict[str, Any]) -> str | None:
    base_url = str(vcfg.get("openai_base_url") or vcfg.get("anthropic_base_url") or "")
    explicit = str(vcfg.get("vision_protocol") or "").strip()
    if explicit in {"openai_cua", "anthropic_cua"}:
        # Relay / OpenAI-compatible endpoints don't implement the native CUA
        # API; degrade to legacy vision (screenshot → vision message) which any
        # Chat Completions endpoint supports.
        return explicit if _is_official_cua_endpoint(explicit, base_url) else None
    backend_type = str(vcfg.get("backend_type") or vcfg.get("backend") or "")
    if backend_type == "anthropic":
        model = vcfg.get("anthropic_model") or vcfg.get("openai_model") or ""
    else:
        model = vcfg.get("openai_model") or vcfg.get("anthropic_model") or ""
    proto = _should_use_native_cua(str(model), backend_type)
    if proto and not _is_official_cua_endpoint(proto, base_url):
        return None
    return proto


def _convert_openai_cua_actions(action: dict[str, Any] | list[Any]) -> list[ScreenAction]:
    """Convert OpenAI computer_call action objects into ScreenAction values."""
    if isinstance(action, list):
        actions: list[ScreenAction] = []
        for item in action:
            if isinstance(item, dict):
                actions.extend(_convert_openai_cua_actions(item))
        return actions
    if not isinstance(action, dict):
        return []

    action_type = str(action.get("type") or action.get("action") or "").strip().lower()
    type_map = {
        "click": ActionType.CLICK,
        "double_click": ActionType.DOUBLE_CLICK,
        "right_click": ActionType.RIGHT_CLICK,
        "type": ActionType.TYPE,
        "keypress": ActionType.KEY,
        "key": ActionType.KEY,
        "scroll": ActionType.SCROLL,
        "drag": ActionType.DRAG,
        "move": ActionType.MOVE,
        "screenshot": ActionType.WAIT,
        "wait": ActionType.WAIT,
    }
    mapped = type_map.get(action_type)
    if not mapped:
        return []

    params: dict[str, Any] = {}
    if mapped in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK, ActionType.MOVE}:
        params["x"] = int(action.get("x") or 0)
        params["y"] = int(action.get("y") or 0)
    elif mapped == ActionType.TYPE:
        params["text"] = str(action.get("text") or "")
    elif mapped == ActionType.KEY:
        params["key"] = str(action.get("key") or action.get("text") or "")
    elif mapped == ActionType.SCROLL:
        params["x"] = int(action.get("x") or 960)
        params["y"] = int(action.get("y") or 540)
        amount = action.get("scroll_y", action.get("amount", action.get("clicks", -3)))
        try:
            params["clicks"] = int(amount)
        except Exception:
            params["clicks"] = -3
    elif mapped == ActionType.DRAG:
        params["start_x"] = int(action.get("start_x", action.get("x", 0)) or 0)
        params["start_y"] = int(action.get("start_y", action.get("y", 0)) or 0)
        path_items = action.get("path") if isinstance(action.get("path"), list) else []
        last = path_items[-1] if path_items and isinstance(path_items[-1], dict) else {}
        params["end_x"] = int(action.get("end_x", last.get("x", 0)) or 0)
        params["end_y"] = int(action.get("end_y", last.get("y", 0)) or 0)
    elif mapped == ActionType.WAIT:
        params["seconds"] = float(action.get("duration", action.get("seconds", 1)) or 1)
    return [ScreenAction(mapped, params, str(action.get("reasoning") or ""))]


def _convert_anthropic_cua_action(action_type: str, inp: dict[str, Any]) -> list[ScreenAction]:
    """Convert Anthropic computer tool action into ScreenAction values."""
    action_type = str(action_type or "").strip().lower()
    type_map = {
        "left_click": ActionType.CLICK,
        "right_click": ActionType.RIGHT_CLICK,
        "double_click": ActionType.DOUBLE_CLICK,
        "type": ActionType.TYPE,
        "key": ActionType.KEY,
        "scroll": ActionType.SCROLL,
        "mouse_move": ActionType.MOVE,
        "screenshot": ActionType.WAIT,
        "wait": ActionType.WAIT,
    }
    mapped = type_map.get(action_type)
    if not mapped:
        return []

    coord = inp.get("coordinate") if isinstance(inp.get("coordinate"), list) else []
    x = int(coord[0]) if len(coord) > 0 else 0
    y = int(coord[1]) if len(coord) > 1 else 0
    params: dict[str, Any] = {}
    if mapped in {ActionType.CLICK, ActionType.RIGHT_CLICK, ActionType.DOUBLE_CLICK, ActionType.MOVE}:
        params.update({"x": x, "y": y})
    elif mapped == ActionType.TYPE:
        params["text"] = str(inp.get("text") or "")
    elif mapped == ActionType.KEY:
        params["key"] = str(inp.get("text") or inp.get("key") or "")
    elif mapped == ActionType.SCROLL:
        params.update({"x": x or 960, "y": y or 540, "clicks": int(inp.get("amount") or -3)})
    elif mapped == ActionType.WAIT:
        params["seconds"] = float(inp.get("duration", inp.get("seconds", 1)) or 1)
    return [ScreenAction(mapped, params)]


def _call_openai_cua(
    png: bytes,
    goal: str,
    action_history: list[dict] | None,
    vcfg: dict[str, Any],
) -> list[ScreenAction]:
    """Call OpenAI Responses API with computer_use_preview and convert actions."""
    import urllib.request

    api_key = str(vcfg.get("openai_api_key") or "").strip()
    base_url = str(vcfg.get("openai_base_url") or "https://api.openai.com/v1").strip().rstrip("/")
    model = str(vcfg.get("openai_model") or "computer-use-preview").strip()
    if not api_key:
        raise RuntimeError("缺少 openai_api_key")
    if not base_url:
        raise RuntimeError("缺少 openai_base_url")

    b64 = _encode_image_base64(png)
    history_text = json.dumps((action_history or [])[-8:], ensure_ascii=False)
    payload = json.dumps(
        {
            "model": model,
            "tools": [
                {
                    "type": "computer_use_preview",
                    "display_width": int(vcfg.get("screen_width") or 1920),
                    "display_height": int(vcfg.get("screen_height") or 1080),
                    "environment": "windows",
                }
            ],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Goal: {goal}\nRecent actions: {history_text}",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{b64}",
                        },
                    ],
                }
            ],
        }
    ).encode()

    url = base_url[:-3] + "/v1/responses" if base_url.endswith("/v1") else base_url + "/responses"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())

    actions: list[ScreenAction] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "computer_call":
            actions.extend(_convert_openai_cua_actions(item.get("action") or item.get("actions") or {}))
        elif item.get("type") == "message":
            content = item.get("content") or []
            text = " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
            if text and not actions:
                lower = text.lower()
                if "done" in lower or "complete" in lower or "完成" in text:
                    actions.append(ScreenAction(ActionType.DONE, {}, text[:200]))
                else:
                    actions.append(ScreenAction(ActionType.FAIL, {"reason": text[:300]}))
    return actions or [ScreenAction(ActionType.FAIL, {"reason": "OpenAI CUA 未返回动作"})]


def _call_anthropic_cua(
    png: bytes,
    goal: str,
    action_history: list[dict] | None,
    vcfg: dict[str, Any],
) -> list[ScreenAction]:
    """Call Anthropic native computer-use tool and convert actions."""
    import urllib.request

    api_key = str(vcfg.get("anthropic_api_key") or "").strip()
    model = str(vcfg.get("anthropic_model") or vcfg.get("openai_model") or "claude-sonnet-4-20250514").strip()
    if not api_key:
        raise RuntimeError("缺少 anthropic_api_key")
    b64 = _encode_image_base64(png)
    history_text = json.dumps((action_history or [])[-8:], ensure_ascii=False)
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 1024,
            "tools": [
                {
                    "type": "computer_20250124",
                    "name": "computer",
                    "display_width_px": int(vcfg.get("screen_width") or 1920),
                    "display_height_px": int(vcfg.get("screen_height") or 1080),
                    "display_number": 1,
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Goal: {goal}\nRecent actions: {history_text}"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    ],
                }
            ],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "computer-use-2025-01-24",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())

    actions: list[ScreenAction] = []
    for block in data.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == "computer":
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            actions.extend(_convert_anthropic_cua_action(str(inp.get("action") or ""), inp))
        elif block.get("type") == "text" and not actions:
            text = str(block.get("text") or "")
            if text:
                actions.append(ScreenAction(ActionType.FAIL, {"reason": text[:300]}))
    return actions or [ScreenAction(ActionType.FAIL, {"reason": "Anthropic CUA 未返回动作"})]


def _try_native_cua(
    protocol: str | None,
    screenshot_png: bytes,
    goal: str,
    action_history: list[dict] | None,
    vcfg: dict[str, Any],
    screen_width: int,
    screen_height: int,
) -> list[ScreenAction] | None:
    if not protocol:
        return None
    vcfg = dict(vcfg)
    vcfg["screen_width"] = screen_width
    vcfg["screen_height"] = screen_height
    try:
        if protocol == "openai_cua":
            return _call_openai_cua(screenshot_png, goal, action_history, vcfg)
        if protocol == "anthropic_cua":
            return _call_anthropic_cua(screenshot_png, goal, action_history, vcfg)
    except Exception as exc:
        desk_log.log("WARN", "screen_reader", f"{protocol} failed, falling back to legacy vision: {exc}")
    return None


def call_vision_llm(
    screenshot_png: bytes,
    goal: str,
    action_history: list[dict] | None = None,
    extra_context: str = "",
    screen_width: int = 0,
    screen_height: int = 0,
) -> ScreenAction:
    """发送截图+目标给多模态 LLM，返回一个 ScreenAction。"""
    vcfg = _load_vision_config()
    backend = vcfg.get("backend", "auto")

    if screen_width == 0 or screen_height == 0:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(screenshot_png))
            screen_width, screen_height = img.size
        except Exception:
            screen_width, screen_height = 1920, 1080

    native_actions = _try_native_cua(
        _native_cua_protocol(vcfg),
        screenshot_png,
        goal,
        action_history,
        vcfg,
        screen_width,
        screen_height,
    )
    if native_actions:
        return native_actions[0]

    user_prompt = _build_user_prompt(goal, action_history, extra_context, screen_width, screen_height)

    if backend == "auto":
        errors = []
        for try_backend in ["ollama", "dashscope", "gemini", "openai"]:
            try:
                raw = _call_backend(try_backend, screenshot_png, user_prompt, vcfg)
                return _parse_action(raw)
            except Exception as e:
                errors.append(f"{try_backend}: {e}")
                continue
        return ScreenAction(ActionType.FAIL, {"reason": f"所有 LLM 后端均不可用: {'; '.join(errors)}"})
    else:
        try:
            raw = _call_backend(backend, screenshot_png, user_prompt, vcfg)
            return _parse_action(raw)
        except Exception as e:
            return ScreenAction(ActionType.FAIL, {"reason": f"LLM 后端 {backend} 调用失败: {e}"})


def _infer_phase(goal: str, action_history: list[dict] | None) -> tuple[str, list[str]]:
    """根据目标和操作历史推断当前阶段，返回 (阶段名, 针对性问题列表)。"""
    if not action_history:
        return "寻找目标", [
            f"截图中能看到与「{goal}」相关的图标、按钮或窗口吗？在哪个位置？",
            "我应该点击哪里来开始这个任务？请给出精确的像素坐标。",
        ]

    actions = [h.get("action", "") for h in action_history]
    last = action_history[-1]
    last_act = last.get("action", "")
    last_params = last.get("params", {})

    had_type = "type" in actions
    had_click = any(a in ("click", "double_click") for a in actions)

    if last_act == "type":
        typed_text = last_params.get("text", "")
        return "验证输入", [
            f"上一步我输入了「{typed_text[:20]}」。请看截图：这些文字出现在屏幕上了吗？",
            "如果文字没有出现，说明输入区域没有获取焦点。请告诉我应该先点击哪个可编辑区域（精确坐标）。",
            "如果文字已经出现，任务完成了吗？下一步应该做什么？",
        ]

    if last_act in ("double_click", "click") and not had_type:
        return "确认打开", [
            f"我的目标是「{goal}」。",
            "上一步我执行了点击操作。请看截图：目标程序/窗口成功打开了吗？",
            "如果已经打开，我现在需要做什么？如果需要输入文字，请告诉我应该点击哪个区域来获取输入焦点（精确坐标）。",
            "如果没有打开成功，请告诉我应该怎么做。",
        ]

    if had_click and not had_type:
        goal_lower = goal.lower()
        need_type = any(
            kw in goal_lower for kw in ["写", "输入", "打字", "发送", "type", "write", "填"]
        )
        if need_type:
            return "准备输入", [
                f"我的目标是「{goal}」，需要输入文字。",
                "请看截图：当前界面中，哪个区域是可以输入文字的编辑区/文本框？",
                "上一步若已 click 过该区域：不要依赖截图里是否看见闪烁光标；**下一步应直接 type 全文**，再在后续截图里用**是否出现已输入文字**判断是否成功。",
                "若尚未 click 过，请给出该区域中心的精确像素坐标以便 click。",
            ]

    return "推进任务", [
        f"我的目标是「{goal}」。请看截图，当前进展如何？",
        "下一步最合理的操作是什么？请给出精确坐标。",
    ]


def _build_user_prompt(
    goal: str,
    action_history: list[dict] | None = None,
    extra_context: str = "",
    screen_width: int = 1920,
    screen_height: int = 1080,
) -> str:
    phase, questions = _infer_phase(goal, action_history)

    parts = [
        f"## 屏幕信息\n"
        f"分辨率: **{screen_width} x {screen_height}** 像素。"
        f"坐标范围: x 从 0 到 {screen_width-1}，y 从 0 到 {screen_height-1}。左上角是 (0,0)。"
    ]

    parts.append(f"## 最终目标\n{goal}")
    parts.append(f"## 当前阶段: {phase}")

    if action_history:
        recent = action_history[-6:]
        lines = []
        for i, h in enumerate(recent, 1):
            act = h.get("action", "?")
            params = h.get("params", {})
            reason = h.get("reasoning", "")
            line = f"{i}. {act}"
            if act in ("click", "double_click", "right_click"):
                line += f" ({params.get('x','?')},{params.get('y','?')})"
            elif act == "type":
                line += f' "{params.get("text","")[:30]}"'
            elif act == "key":
                line += f" [{params.get('key','')}]"
            elif act == "hotkey":
                line += f" [{'+'.join(params.get('keys',[]))}]"
            if reason:
                line += f" — {reason}"
            lines.append(line)
        parts.append("## 已执行的操作\n" + "\n".join(lines))

    parts.append("## 请回答以下问题\n" + "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions)))

    if extra_context:
        parts.append(f"## 额外信息\n{extra_context}")

    parts.append(
        "## 最后：给出下一步操作（JSON）\n"
        "先回答上面的问题（**观察**和**回答**），然后给出一个 JSON 操作。\n"
        f"坐标必须是 0~{screen_width-1}(x) 和 0~{screen_height-1}(y) 范围内的整数。"
    )

    return "\n\n".join(parts)


def _parse_action(raw_text: str) -> ScreenAction:
    """从 LLM 文本响应中提取 JSON action。支持嵌套 JSON 和代码块。"""
    text = raw_text.strip()

    code_match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
    if code_match:
        text = code_match.group(1)

    candidates = _extract_json_objects(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "action" in data:
            try:
                action_type = ActionType(data["action"])
            except ValueError:
                action_type = ActionType.FAIL
            return ScreenAction(
                action=action_type,
                params=data.get("params", {}),
                reasoning=data.get("reasoning", ""),
            )

    return ScreenAction(ActionType.FAIL, {"reason": f"LLM 响应无法解析为动作: {raw_text[:200]}"})


def _extract_json_objects(text: str) -> list[str]:
    """从文本中提取所有顶层 JSON 对象（处理嵌套大括号）。"""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            in_str = False
            escape = False
            while i < len(text):
                ch = text[i]
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = not in_str
                elif not in_str:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            results.append(text[start:i + 1])
                            break
                i += 1
        i += 1
    return results


# ─── 后端实现 ───

def _call_backend(backend: str, png: bytes, user_prompt: str, vcfg: dict) -> str:
    if backend == "ollama":
        return _call_ollama(png, user_prompt, vcfg)
    elif backend == "dashscope":
        return _call_dashscope(png, user_prompt, vcfg)
    elif backend == "gemini":
        return _call_gemini(png, user_prompt, vcfg)
    elif backend == "openai":
        return _call_openai(png, user_prompt, vcfg)
    raise RuntimeError(f"未知后端: {backend}")


def _call_ollama(png: bytes, prompt: str, vcfg: dict) -> str:
    """本地 Ollama（llava / bakllava / minicpm-v 等）。"""
    import urllib.request
    host = vcfg.get("ollama_host", "http://localhost:11434")
    model = vcfg.get("ollama_model", "llava")

    payload = json.dumps({
        "model": model,
        "prompt": SYSTEM_PROMPT + "\n\n" + prompt,
        "images": [_encode_image_base64(png)],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data.get("response", "")


def _call_dashscope(png: bytes, prompt: str, vcfg: dict) -> str:
    """阿里云百炼 Qwen-VL。"""
    api_key = (vcfg.get("dashscope_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 dashscope_api_key（请在 web/config.py 配置 DASHSCOPE_API_KEY）")
    model = (vcfg.get("dashscope_model") or "qwen-vl-max").strip()
    endpoint = (vcfg.get("dashscope_native_multimodal_url") or "").strip()
    if not endpoint:
        raise RuntimeError("缺少 dashscope_native_multimodal_url（web/config.py）")

    import urllib.request
    b64 = _encode_image_base64(png)
    payload = json.dumps({
        "model": model,
        "input": {
            "messages": [
                {"role": "system", "content": [{"text": SYSTEM_PROMPT}]},
                {"role": "user", "content": [
                    {"image": f"data:image/png;base64,{b64}"},
                    {"text": prompt},
                ]},
            ]
        },
    }).encode()

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    choices = data.get("output", {}).get("choices", [{}])
    if choices:
        content = choices[0].get("message", {}).get("content", [])
        return content[0].get("text", "") if content else ""
    return ""


def _call_gemini(png: bytes, prompt: str, vcfg: dict) -> str:
    """Google Gemini Vision API。"""
    api_key = (vcfg.get("gemini_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 gemini_api_key（请在 web/config.py 配置 GEMINI_API_KEY）")
    model = (vcfg.get("gemini_model") or "gemini-2.0-flash").strip()
    tpl = (vcfg.get("gemini_generate_url_template") or "").strip()
    if not tpl:
        raise RuntimeError("缺少 gemini_generate_url_template（web/config.py）")

    import urllib.request
    b64 = _encode_image_base64(png)
    payload = json.dumps({
        "contents": [{
            "parts": [
                {"text": SYSTEM_PROMPT + "\n\n" + prompt},
                {"inline_data": {"mime_type": "image/png", "data": b64}},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }).encode()

    req = urllib.request.Request(
        f"{tpl.format(model=model)}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else ""
    return ""


def _get_ssl_context():
    """SSL 容错：先用默认验证，失败则降级为不验证（国内云 API 常见证书问题）。"""
    import ssl
    try:
        ctx = ssl.create_default_context()
        return ctx
    except Exception:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _call_openai(png: bytes, prompt: str, vcfg: dict) -> str:
    """OpenAI GPT-4o / 兼容接口（也可指向 DashScope、DeepSeek 等兼容端点）。"""
    api_key = (vcfg.get("openai_api_key") or "").strip()
    base_url = (vcfg.get("openai_base_url") or "").strip().rstrip("/")
    model = (vcfg.get("openai_model") or "").strip()

    if not api_key:
        raise RuntimeError("缺少 openai_api_key（请在 web/config.py 配置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY）")
    if not base_url:
        raise RuntimeError("缺少 openai_base_url（web/config.py 的 OPENAI_BASE_URL）")
    if not model:
        raise RuntimeError("缺少 openai_model（web/config.py 的 OPENAI_VISION_MODEL）")

    import ssl
    import urllib.request
    b64 = _encode_image_base64(png)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }).encode()

    url = f"{base_url}/chat/completions"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except (ssl.SSLError, ssl.SSLCertVerificationError, OSError):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = json.loads(resp.read())

    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return ""


# ─── 批量规划（human-multimodal 核心）───

BATCH_SYSTEM_PROMPT = """你是 Windows 电脑操作助手。你看到屏幕截图，需要把用户的目标分解为一批具体的鼠标/键盘操作步骤。

## 回答格式

返回一个 JSON **数组**，包含 2~8 个按顺序执行的操作:
```json
[
  {"action": "类型", "params": {参数}, "target_text": "要点击的文字", "reasoning": "说明"},
  ...
]
```

## 操作类型

| action | params | target_text |
|--------|--------|-------------|
| click | {"x": 数字, "y": 数字} | 附近的文字标签（用于精确定位） |
| double_click | {"x": 数字, "y": 数字} | 附近的文字标签 |
| right_click | {"x": 数字, "y": 数字} | 附近的文字标签 |
| type | {"text": "文字"} | 不需要 |
| key | {"key": "键名"} | 不需要 |
| hotkey | {"keys": ["ctrl", "c"]} | 不需要 |
| scroll | {"clicks": 数字, "x": 数字, "y": 数字} | 不需要 |
| drag | {"start_x", "start_y", "end_x", "end_y"} | 不需要 |
| wait | {"seconds": 数字} | 不需要 |
| done | {} | 不需要 |

## 关键规则

1. **坐标 = 像素坐标**，左上角 (0,0)，屏幕分辨率会在问题中告知。
2. **target_text 非常重要**：对于 click / double_click / right_click，必须填写你想点击的按钮/图标/文字的标签文字。系统会用 OCR 校准你给的坐标，保证精确点击。
3. **type 之前必须先 click 输入区域获取焦点**。
4. **桌面图标**：double_click 图标图片中心（文字标签上方约 35px），不要点文字。target_text 填图标下方的文字名称。
5. 步骤之间如果需要等待界面加载，插入 wait。
6. 只规划你**确定能从截图中看到**的操作，不要猜测看不见的界面。
7. 任务完成后最后一步用 done。
"""


def call_vision_llm_batch(
    screenshot_png: bytes,
    goal: str,
    action_history: list[dict] | None = None,
    extra_context: str = "",
    screen_width: int = 0,
    screen_height: int = 0,
) -> list[ScreenAction]:
    """多模态批量规划：一次 API 调用返回多个按序执行的步骤。"""
    vcfg = _load_vision_config()
    backend = vcfg.get("backend", "auto")
    desk_log.log("INFO", "screen_reader", f"call_vision_llm_batch: backend={backend} goal={goal[:60]}")
    desk_log.log(
        "DEBUG",
        "screen_reader",
        f"vcfg={json.dumps(_vision_cfg_for_log(vcfg), ensure_ascii=False, default=str)}",
    )

    if screen_width == 0 or screen_height == 0:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(screenshot_png))
            screen_width, screen_height = img.size
        except Exception:
            screen_width, screen_height = 1920, 1080

    desk_log.log("INFO", "screen_reader", f"screen={screen_width}x{screen_height} png_size={len(screenshot_png)}")

    native_actions = _try_native_cua(
        _native_cua_protocol(vcfg),
        screenshot_png,
        goal,
        action_history,
        vcfg,
        screen_width,
        screen_height,
    )
    if native_actions:
        desk_log.log("INFO", "screen_reader", f"native CUA returned {len(native_actions)} actions")
        return native_actions

    phase, questions = _infer_phase(goal, action_history)
    desk_log.log("INFO", "screen_reader", f"phase={phase} questions={questions}")

    parts = [
        f"屏幕分辨率: {screen_width} x {screen_height} 像素。"
        f"坐标范围: x=0~{screen_width-1}, y=0~{screen_height-1}。",
        f"## 最终目标\n{goal}",
        f"## 当前阶段: {phase}",
    ]

    if action_history:
        recent = action_history[-6:]
        lines = []
        for i, h in enumerate(recent, 1):
            act = h.get("action", "?")
            p = h.get("params", {})
            r = h.get("reasoning", "")
            line = f"{i}. {act}"
            if act in ("click", "double_click"):
                line += f" ({p.get('x','?')},{p.get('y','?')})"
            elif act == "type":
                line += f' "{p.get("text","")[:30]}"'
            if r:
                line += f" — {r}"
            lines.append(line)
        parts.append("## 已执行\n" + "\n".join(lines))

    if extra_context:
        parts.append(f"## 额外信息\n{extra_context}")

    parts.append("## 请回答\n" + "\n".join(f"- {q}" for q in questions))
    parts.append(
        f"## 规划接下来的操作（JSON 数组，2~8 步）\n"
        f"坐标必须在 0~{screen_width-1}(x), 0~{screen_height-1}(y) 范围内。"
    )

    user_prompt = "\n\n".join(parts)
    desk_log.log("DEBUG", "screen_reader", f"user_prompt (len={len(user_prompt)}):\n{user_prompt[:1500]}")

    raw = ""
    t0 = time.time()
    api_error = ""

    try:
        if backend == "auto":
            for try_be in ["ollama", "dashscope", "gemini", "openai"]:
                try:
                    desk_log.log("INFO", "screen_reader", f"尝试后端: {try_be}")
                    raw = _call_backend_with_system(try_be, screenshot_png, user_prompt, BATCH_SYSTEM_PROMPT, vcfg)
                    desk_log.log("INFO", "screen_reader", f"后端 {try_be} 成功, 响应长度={len(raw)}")
                    break
                except Exception as be_err:
                    desk_log.log("WARN", "screen_reader", f"后端 {try_be} 失败: {be_err}")
                    api_error += f"{try_be}: {be_err}; "
                    continue
        else:
            desk_log.log("INFO", "screen_reader", f"直接调用后端: {backend}")
            raw = _call_backend_with_system(backend, screenshot_png, user_prompt, BATCH_SYSTEM_PROMPT, vcfg)
            desk_log.log("INFO", "screen_reader", f"后端 {backend} 成功, 响应长度={len(raw)}")
    except Exception as e:
        api_error = str(e)
        desk_log.log("ERROR", "screen_reader", f"所有后端失败: {e}")
        desk_log.log_exception("screen_reader", "call_vision_llm_batch API 调用全部失败")
        elapsed = time.time() - t0
        desk_log.log_api_call(backend, user_prompt, BATCH_SYSTEM_PROMPT, "", elapsed, api_error)
        return [ScreenAction(ActionType.FAIL, {"reason": f"batch API 失败: {e}"})]

    elapsed = time.time() - t0
    desk_log.log_api_call(backend, user_prompt, BATCH_SYSTEM_PROMPT, raw, elapsed, api_error)
    desk_log.log("INFO", "screen_reader", f"API 原始响应 (elapsed={elapsed:.2f}s):\n{raw[:2000]}")

    result = _parse_action_batch(raw)
    desk_log.log("INFO", "screen_reader", f"解析出 {len(result)} 个 action")
    desk_log.log_batch_plan(result, raw)
    return result


def _call_backend_with_system(backend: str, png: bytes, prompt: str, system: str, vcfg: dict) -> str:
    """与 _call_backend 相同，但允许自定义 system prompt。"""
    if backend == "openai":
        return _call_openai_with_system(png, prompt, system, vcfg)
    original = globals().get("SYSTEM_PROMPT", "")
    globals()["SYSTEM_PROMPT"] = system
    try:
        return _call_backend(backend, png, prompt, vcfg)
    finally:
        globals()["SYSTEM_PROMPT"] = original


def _call_openai_with_system(png: bytes, prompt: str, system: str, vcfg: dict) -> str:
    """OpenAI 兼容接口，自定义 system prompt。"""
    api_key = (vcfg.get("openai_api_key") or "").strip()
    base_url = (vcfg.get("openai_base_url") or "").strip().rstrip("/")
    model = (vcfg.get("openai_model") or "").strip()

    desk_log.log("INFO", "openai", f"model={model} base_url={base_url} key={'***' + api_key[-6:] if len(api_key) > 6 else '?'}")

    if not api_key:
        desk_log.log("ERROR", "openai", "缺少 API KEY")
        raise RuntimeError("缺少 API KEY")

    import ssl
    import urllib.request
    b64 = _encode_image_base64(png)
    desk_log.log("DEBUG", "openai", f"image base64 length={len(b64)}")

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }).encode()

    if not base_url:
        desk_log.log("ERROR", "openai", "缺少 openai_base_url")
        raise RuntimeError("缺少 openai_base_url（web/config.py）")
    if not model:
        desk_log.log("ERROR", "openai", "缺少 openai_model")
        raise RuntimeError("缺少 openai_model（web/config.py）")

    url = f"{base_url}/chat/completions"
    desk_log.log("INFO", "openai", f"POST {url} payload_size={len(payload)}")

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    t0 = time.time()
    data = None
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw_body = resp.read()
            data = json.loads(raw_body)
            desk_log.log("INFO", "openai", f"HTTP OK elapsed={time.time()-t0:.2f}s resp_size={len(raw_body)}")
    except (ssl.SSLError, ssl.SSLCertVerificationError, OSError) as ssl_err:
        desk_log.log("WARN", "openai", f"SSL/网络错误: {ssl_err}, 尝试跳过证书验证…")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
                raw_body = resp.read()
                data = json.loads(raw_body)
                desk_log.log("INFO", "openai", f"SSL bypass OK elapsed={time.time()-t0:.2f}s resp_size={len(raw_body)}")
        except Exception as retry_err:
            desk_log.log("ERROR", "openai", f"SSL bypass 也失败: {retry_err}")
            desk_log.log_exception("openai", "API 调用彻底失败")
            raise
    except Exception as e:
        desk_log.log("ERROR", "openai", f"HTTP 请求失败: {e}")
        desk_log.log_exception("openai", "API 调用失败")
        raise

    if data is None:
        desk_log.log("ERROR", "openai", "data is None (不应出现)")
        return ""

    choices = data.get("choices", [])
    usage = data.get("usage", {})
    desk_log.log("INFO", "openai", f"choices={len(choices)} usage={usage}")

    if choices:
        content = choices[0].get("message", {}).get("content", "")
        desk_log.log("DEBUG", "openai", f"response content (len={len(content)}): {content[:500]}")
        return content

    desk_log.log("WARN", "openai", f"无 choices，完整响应: {json.dumps(data, ensure_ascii=False)[:1000]}")
    return ""


def _parse_action_batch(raw_text: str) -> list[ScreenAction]:
    """从 LLM 响应中提取 JSON 数组，解析为多个 ScreenAction。"""
    text = raw_text.strip()

    code_match = re.search(r'```(?:json)?\s*(\[.+?\])\s*```', text, re.DOTALL)
    if code_match:
        text = code_match.group(1)
    else:
        arr_match = re.search(r'\[[\s\S]*\]', text)
        if arr_match:
            text = arr_match.group(0)

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        single = _parse_action(raw_text)
        return [single]

    if not isinstance(items, list):
        single = _parse_action(raw_text)
        return [single]

    result: list[ScreenAction] = []
    for item in items:
        if not isinstance(item, dict) or "action" not in item:
            continue
        try:
            action_type = ActionType(item["action"])
        except ValueError:
            continue
        params = item.get("params", {})
        target_text = item.get("target_text", "")
        if target_text:
            params["_target_text"] = target_text
        result.append(ScreenAction(
            action=action_type,
            params=params,
            reasoning=item.get("reasoning", ""),
        ))

    return result if result else [_parse_action(raw_text)]
