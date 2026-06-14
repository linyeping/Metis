# -*- coding: utf-8 -*-
"""操作循环：三种模式执行自然语言指令。

模式（exec_mode）:

  auto  = 先 skill（程序化 API/CLI），不行再 human 智能链
  human = **类人键鼠**，human_core 默认 **som**（ROI+SoM）；可选 llm / multimodal
  skill = OpenClaw 式：只走程序化/技能链，不调 human 视觉链
"""

from __future__ import annotations

import io
import json
import re
import ssl
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image, ImageDraw

from .. import config
from ..capture.scaling import capture_roi_png, prepare_dual_api_payload
from ..capture.screenshot import grab_screen_png
from ..input.actions import (
    SomFrameContext,
    _normalize_element_id,
    click_at,
    double_click,
    drag_to,
    execute_som_action,
    hotkey,
    move_to,
    press_key,
    right_click,
    scroll,
    type_chinese,
    type_text,
)
from .screen_parser import parse_roi_l2, uielement_list_to_prompt_lines
from .screen_reader import (
    ActionType,
    ScreenAction,
    call_vision_llm,
    call_vision_llm_batch,
    _call_backend_with_system,
    _encode_image_base64,
    _extract_json_objects,
    _load_vision_config,
)
from .programmatic import try_programmatic
from .nlu import classify_intent
from . import desk_log
from .frame_diff import (
    compare_frames,
    compose_rois_for_prompt,
    crop_hotspots_to_png,
    should_throttle_api,
)
from .ocr_locate import find_click_by_ocr, ocr_scan_all, ocr_calibrate_click
from .env_scanner import format_env_for_prompt, startup_scan
from . import skill_followup
from . import task_state
from ..hooks.interrupt_manager import (
    is_esc_stop_set,
    reset_esc_stop_for_new_run,
    start_esc_listener,
    register_interrupt_callback,
)


@dataclass
class VisionLoopState:
    """操作循环的运行时状态。"""
    goal: str = ""
    status: str = "idle"  # idle | running | paused | done | error
    exec_mode: str = "auto"  # auto | human | skill
    human_core: str = "som"  # som | llm | multimodal
    step_count: int = 0
    max_steps: int = 50
    action_history: list[dict] = field(default_factory=list)
    last_screenshot_path: str = ""
    vision_raw_latest_path: str = ""
    vision_som_latest_path: str = ""
    vision_artifacts_dir: str = ""
    last_action: dict = field(default_factory=dict)
    error: str = ""
    start_time: float = 0.0
    prev_frame_png: bytes | None = None
    last_vision_api_time: float = 0.0
    stable_frame_streak: int = 0
    skill_followup_notified: bool = False
    vision_api_calls: int = 0
    frozen_skip_streak: int = 0
    idle_continue_count: int = 0
    step_budget_warned: bool = False
    # 供 /api/vision/state 返回：完整可审计步进（含 LLM observation 等），与 action_history 同步增长
    vision_event_log: list[dict[str, Any]] = field(default_factory=list)


_VISION_EVENT_LOG_MAX: int = 200
_VISION_ACTION_HISTORY_API_MAX: int = 120


_state = VisionLoopState()
_thread: threading.Thread | None = None
_stop = threading.Event()
# 用户「终止任务」：各 human 子循环每轮开头检查，优先于普通 interrupt 的 paused 语义
_run_aborted = threading.Event()

# 回调：每步执行后调用，用于 HTTP SSE / WebSocket 推送
_on_step: Callable[[dict], None] | None = None


class InterruptManager:
    """急停：vision API stop + 磁盘 pause + pynput ESC（hooks/interrupt_manager）。"""

    def is_interrupted(self) -> bool:
        return _stop.is_set() or config.is_paused() or is_esc_stop_set()


interrupt_manager = InterruptManager()

_interrupt_sse_registered = False


def _ensure_interrupt_sse_bridge() -> None:
    """ESC 时把事件转发给 on_step，便于 SSE/WebSocket 立刻弹窗。"""
    global _interrupt_sse_registered
    if _interrupt_sse_registered:
        return

    def _sink(payload: dict[str, Any]) -> None:
        fn = _on_step
        if not fn:
            return
        try:
            fn(
                {
                    **payload,
                    "vision_status": _state.status,
                    "step": _state.step_count,
                    "goal": _state.goal,
                }
            )
        except Exception:
            pass

    try:
        register_interrupt_callback(_sink)
        _interrupt_sse_registered = True
    except Exception:
        pass


def _push_desk_sse() -> None:
    """将当前视觉循环快照推给所有 SSE 客户端（顶栏 Vision 徽章即时更新）。"""
    try:
        from backend.web import desk_sse

        desk_sse.broadcast(
            {
                "event": "vision_state",
                "vision_status": _state.status,
                "vision_running": is_running(),
                "vision_goal": _state.goal,
                "vision_step": _state.step_count,
                "vision_max_steps": _state.max_steps,
            }
        )
    except Exception:
        pass


def _push_desk_sse_thread_finished() -> None:
    """视觉线程即将退出时在 finally 中调用：此时 is_running() 仍为 True，故显式 vision_running=False。"""
    try:
        from backend.web import desk_sse

        desk_sse.broadcast(
            {
                "event": "vision_state",
                "vision_status": _state.status,
                "vision_running": False,
                "vision_goal": _state.goal,
                "vision_step": _state.step_count,
                "vision_max_steps": _state.max_steps,
            }
        )
    except Exception:
        pass


def _thread_entry(extra_context: str) -> None:
    """线程入口：无论 _loop 正常结束或异常退出，最后推送一次最终状态。"""
    try:
        _loop(extra_context)
    finally:
        _push_desk_sse_thread_finished()


def _consume_run_abort() -> bool:
    """若用户已请求终止，将状态置 idle 并收尾日志。返回 True 表示本循环应立即 return。"""
    if not _run_aborted.is_set():
        return False
    _run_aborted.clear()
    _stop.clear()
    _state.status = "idle"
    _state.error = ""
    task_state.append_log("warn", "[vision] 用户终止任务")
    task_state.finish_goal(False, "用户终止")
    try:
        _som_desk_end_run(False, "user_terminate")
    except Exception:
        pass
    _push_desk_sse()
    return True


def _tail_same_click_element_id(history: list[dict]) -> tuple[str, int]:
    """从末尾向前数，连续 click 且同一 element_id 的次数（用于网页输入框 OCR 误框死循环）。"""
    eid_last: str | None = None
    n = 0
    for h in reversed(history):
        if str(h.get("action", "")).lower() != "click":
            break
        p = h.get("params") if isinstance(h.get("params"), dict) else {}
        eid = h.get("element_id") or p.get("element_id")
        if eid is None:
            break
        s = str(eid).strip()
        if not s or s.lower() in ("null", "none"):
            break
        if eid_last is None:
            eid_last = s
            n = 1
        elif s == eid_last:
            n += 1
        else:
            break
    return (eid_last or "", n)


def detect_loop(history: list[dict]) -> str:
    """交接 part_07 §六：死循环信号检测文案，注入 SoM User Message。"""
    if len(history) < 2:
        return "正常，未检测到死循环（历史不足 2 步）。"
    eid_rep, rep_n = _tail_same_click_element_id(history)
    if rep_n >= 3:
        return (
            f"🛑 严重：最近连续 {rep_n} 次 click 均为同一 element_id={eid_rep!r}，极可能 SoM 框在占位字上而非可编辑区。"
            "**禁止**再对该编号 click。**必须先**对任务目标执行一次乐观 **`type`**（不要等截图里出现光标），再在下一轮用图①**是否出现已输入文字**判断是否成功；失败才换 x,y / hotkey 后重试。"
        )
    last2 = history[-2:]
    if (
        last2[0].get("action") == last2[1].get("action")
        and last2[0].get("element_id") == last2[1].get("element_id")
    ):
        return (
            f"⚠️ 检测到死循环：连续 2 步为同一 action={last2[0].get('action')!r} "
            f"+ element_id={last2[0].get('element_id')!r}，result={last2[1].get('result')!r}。"
            "必须改变策略或委托外部 AI。"
        )
    if all(h.get("action") == "click" for h in history[-3:]):
        return "⚠️ 警告：连续 3 步均为 click，请确认界面是否真正响应。"
    return "正常，未检测到明显死循环。"


def _goal_suggests_text_entry(goal: str) -> bool:
    """目标是否涉及向界面键入文字（用于注入「乐观 type / 看字校验」提示）。"""
    if not (goal or "").strip():
        return False
    g = goal.strip().lower()
    keys = (
        "输入",
        "打字",
        "发送",
        "键入",
        "写入",
        "填",
        "留言",
        "告诉",
        "type",
        "write",
    )
    return any(k in g for k in keys)


def _som_post_action_verify_section(history: list[dict], goal: str = "") -> list[str]:
    """在用户消息中注入「上一步 vs 当前截图」自检说明（通用，非单任务写死）。"""
    if not history:
        return []
    last = history[-1]
    act = str(last.get("action", "")).strip()
    if act in ("done", "fail", "ask_user", ""):
        return []
    step_no = last.get("step", "?")
    rsn = (last.get("reasoning") or "").strip()
    if len(rsn) > 200:
        rsn = rsn[:200] + "…"
    snap = str(last.get("target_snapshot") or "").strip()
    tgt = last.get("element_id")
    params = last.get("params") if isinstance(last.get("params"), dict) else {}
    if not tgt and isinstance(params, dict) and "x" in params and "y" in params:
        tgt = f"API({params.get('x')},{params.get('y')})"
    tgt_s = str(tgt or params.get("element_id") or "")
    res = str(last.get("result", ""))
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  【本轮优先】上一步结果 vs 当前截图",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"系统已执行：step#{step_no} | action={act} | 目标={tgt_s or '（见 params）'}",
    ]
    if snap:
        lines.append(f"SoM 当时解析快照：{snap}")
    ge = _goal_suggests_text_entry(goal)
    obs_extra: list[str] = []
    if ge and act in ("click", "double_click"):
        obs_extra.append(
            "  · **（输入任务 · 乐观法）**若上一步意在聚焦输入框：**禁止**因「图①里看不见闪烁光标」而拒绝输出 `type`；光标**不得**作为是否允许打字的前置条件。本步应直接 `type` 用户要求的全文（可先 `wait` 0.2～0.5s），下一轮再用**图中是否出现已输入文字**判断焦点是否成功。"
        )
    if ge and act == "type":
        tp = ""
        if isinstance(params, dict):
            tp = str(params.get("text", ""))[:80]
        obs_extra.append(
            "  · **（输入任务）**上一步为 `type`：请在 observation 中说明图①**是否出现**刚输入的文字"
            + (f"（例如含「{tp}」）" if tp else "")
            + "；以**文字是否可见（OCR）**为成功依据，**不要**讨论光标是否清晰。"
        )
    lines.extend(
        [
            f"上一步 reasoning：{rsn or '（无）'}",
            f"执行返回值：{res}",
            "",
            "你必须在本轮 JSON 的 observation 中**首先**回答：",
            "  · 根据**当前图①**，上一步**预期**的界面变化是否已经出现？",
        ]
    )
    lines.extend(obs_extra)
    lines.extend(
        [
            "  · 若未出现或明显不对，用可见证据说明，并给出纠错动作；禁止无视截图继续假设成功。",
            "",
        ]
    )
    return lines


def _som_target_snapshot(
    elements: list[Any],
    elem_id: Any,
    params: dict[str, Any],
) -> str:
    """把本步点击目标对应到 SoM 列表的 type/content，写入日志便于对照「以为点了桌面其实点了下载」。"""
    eid = _normalize_element_id(
        str(params.get("element_id") or elem_id or "").strip() or None
    )
    if eid:
        for el in elements:
            if getattr(el, "element_id", None) == eid:
                ct = (getattr(el, "content", "") or "")[:120]
                typ = getattr(el, "type", "?")
                return f"{eid} type={typ} content={ct!r}"
        return f"{eid}（本帧元素列表中未找到，可能编号已变）"
    if "x" in params and "y" in params:
        return f"API坐标 ({params.get('x')},{params.get('y')})"
    return "无 element_id / 无 x,y"


# ─── Human / SoM 双图模式（part_07 User Message 模板 + L2 解析） ───

HUMAN_SOM_SYSTEM = """你是 Miro，运行在 Windows 上的纯键鼠 Agent。你收到两张同一 ROI 的图：
【图① 原图】只用于阅读文字、代码、报错；不要依赖图②读字。
【图② SoM 图】带红框与 ~1、~2 编号；**能对应到某个框时**点击类动作应优先用 element_id（如 ~3）。
**空白处无编号**：桌面壁纸、菜单外的纯色区等往往没有 ~n。若任务要求在空白处操作（例如桌面空白处 **right_click** 打开「新建」菜单），图②没有可点编号时，**必须在 params 里给出整数 "x","y"（API 坐标，与 api_w×api_h 同源）**，指向图①中肉眼可见的空白区域中心；禁止为了不写坐标而硬选一个无关的 element_id。「禁止臆测」指不要编造与画面无关的坐标；在已知画幅下根据图①估计空白区中心是正确做法。

**感知与纠错（通用，适用于所有任务）**
- 每一轮附带的两张图，都是**系统执行完你上一条动作之后**重新截取的最新画面。你必须根据**当前**图①判断真实世界状态，**禁止**默认「上一步已经成功」。
- 字段 **observation** 的第一职责：对照**上一步意图**与**当前图①**，写明「预期界面是否出现」；若未出现或明显不对（打开了错误窗口、进了错误目录、点的不是目标控件），必须在 observation 里写出**可见证据**，并在本步 action 中选择纠错路径（关闭、后退、重选 element_id、另选坐标等），**禁止**在错误状态下继续假装推进。
- 用户消息里会给出「上一步执行摘要」；你必须把该摘要与**当前截图**交叉验证。若连续多步界面与任务目标偏离，优先 `screenshot` 或 `wait` 再决策，避免盲点对同一错误假设。
- SoM 列表里的 **content** 常为 OCR 碎片，可能与真实标签、图标不一致；**以图①完整文字与图标类型为准**。名称相似、位置相邻的项（列表行、侧栏、桌面图标）极易混淆，选 `element_id` 前必须在图①中核对。
- **文本输入（乐观法，禁止「光标门控」）**：截图**极难**稳定捕获闪烁的文本插入条。**禁止**把「图①里是否看得见光标」当作执行 `type` 的前置条件；**禁止**仅因「好像没光标」就在未尝试输入前对同一位置反复 `click`。正确流程：(1) 对占位符/输入条执行 `click` 或 `double_click`；(2) **紧接着应输出 `type`**，写入完整 `params.text`（可先 `wait` 0.2～0.5s，勿久等「等光标」）；(3) **输入后**在 observation 用图①**是否出现刚打的字**（OCR）判断成败——**看字不看光标**；(4) 仅当**明确看不到**已输入文字时，再换坐标/element 重试点击并再次 `type`。
- **网页聊天（Gemini / ChatGPT 等）**：底栏多为 contenteditable。遵守上文**乐观 type + 看字校验**。若对同一 `element_id` **连点多次却从不 `type`**，属于错误策略。仅当**已做过乐观 `type`** 且图①仍**看不到**目标文字、且同一 element **click≥2** 时，才禁止再点该编号，改用 **x,y** 或 **hotkey**。`wait` 填 **params.seconds** 或 **params.timeout**（秒）。
- **Gemini 桌面端（易错）**：可输入区域是**圆角大矩形内的占位字**（如「问问 Gemini 3」），点矩形**内部偏上**，避开 +、麦克风等。**屏幕最底一行小号灰字**（免责声明）不是输入框，**禁止**对其 click 来输入。点击输入区后**不要等光标可见**，直接 `type`；用图中是否出现所打文字判断是否需要重试。
- **发送 / 提交消息**：当目标包含「发送、提交、发出」等且已在输入区 **type** 完并**确认文字无误**后，**优先**使用 `{"action":"key","params":{"key":"enter"}}` 发送。纸飞机/箭头类**发送按钮**往往小且与背景对比弱，**OCR/SoM 常识别不到**；**不要**把「必须先点到发送按钮」当作默认路径。仅当 Enter 后下一帧截图显示消息仍未发出时，再尝试 click 发送控件。

坐标系：所有数字坐标均在「API 坐标系」内，尺寸见用户消息中的 api_w × api_h（与图①②一致）。
输出：仅一个 JSON 对象（不要 markdown 围栏外再写文字），字段示例：
{
  "observation": "上一步 double_click ~5 预期打开记事本；图①实为资源管理器「下载」文件夹 → 未达成，点错侧栏项",
  "history_check": "对照历史是否死循环",
  "element_confirmed": "~3 或 null",
  "action": "click|double_click|right_click|type|key|hotkey|scroll|drag|drag_abs|wait|wait_clipboard_change|delegate_to_ai|screenshot|done|fail|ask_user",
  "reasoning": "一句话",
  "params": { }
}
click/double_click/right_click：有合适编号用 "element_id":"~n"；**仅当空白/无框可指时**用 "x","y"（API 像素，整数）。
scroll: {"direction":"down","clicks":3, "element_id":"~4"} ；无 element 则在 ROI 中心滚。
drag: {"from_element_id":"~1","to_element_id":"~8"}；drag_abs: {"from_api":[x,y],"to_api":[x,y]}。
wait_clipboard_change: {"timeout":5.0}；delegate_to_ai: {"query_summary":"..."}；fail: params.reason；ask_user: params.question。
"""


def _build_som_user_message(
    goal: str,
    roi_label: str,
    api_w: int,
    api_h: int,
    elements: list[Any],
    scale_factor: float,
    history: list[dict],
    loop_warn: str,
    extra: str,
    history_n: int = 5,
) -> str:
    """按 part_07「三、每步 User Message 完整模板」组文本（图片由多模态通道单独附）。"""
    el_lines = uielement_list_to_prompt_lines(elements, scale_factor=scale_factor)
    elements_block = "\n".join(el_lines) if el_lines else "  （未检测到元素）"

    recent = history[-history_n:]
    hist_lines = []
    for i, h in enumerate(recent, 1):
        step_no = h.get("step", i)
        act = h.get("action", "?")
        tgt = h.get("element_id") or h.get("params", {})
        if isinstance(tgt, dict):
            tgt = tgt.get("element_id") or tgt.get("text") or str(tgt)[:40]
        res = h.get("result", "")
        rsn = (h.get("reasoning") or "")[:60]
        hist_lines.append(
            f"  #{step_no} | {act:16} | {tgt!s:24} | {res:8} | {rsn}"
        )
    history_block = "\n".join(hist_lines) if hist_lines else "  （尚无历史）"

    parts = [
        "════════ 图① 原图（阅读用）与 图② SoM（选编号用）已随本消息附上 ════════",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  屏幕区域信息",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"当前 ROI：{roi_label}",
        f"API 坐标系尺寸：{api_w} × {api_h}",
        "  · 图② 中编号元素的 bbox 均在此坐标系下；选 element_id 后由系统映射到物理像素。",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  最终任务目标",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        goal,
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  ⚠️ 最近 {history_n} 步操作历史 + 死循环检测",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        history_block,
        "",
        "死循环检测提示：",
        loop_warn,
        "",
    ]
    eid_rep, rep_n = _tail_same_click_element_id(history)
    if rep_n >= 2:
        parts.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "  🛑 硬性提醒（系统根据历史自动生成）",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"你对 {eid_rep!r} 已连续 click {rep_n} 次。**若尚未乐观执行过 `type`**，本步必须先 `type` 全文再判；**禁止**再等光标。若**已经 `type` 过**且图中仍无目标文字，**本轮不得再对该 element_id 发 click**；请改用 x,y / hotkey / fail。",
                "",
            ]
        )
    parts.extend(
        [
            *_som_post_action_verify_section(history, goal=goal),
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "  当前阶段判断（系统占位，可忽略或自行修正）",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "阶段：推进任务",
            "提示：先完成上面对「上一步 vs 当前图」的自检，再根据图①判断进度，用图②选择 element_id。",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  UI 元素列表（共 {len(elements)} 个，对应图②编号）",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "格式：编号 | 类型 | 内容 | bbox中心(API坐标)",
            elements_block,
            "",
            "选框：content 可能为 OCR 碎片，**与图①图标+全文核对**后再选编号。",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "  请完成观察→历史检查→定位→决策后，仅输出一个 JSON（见 system 说明）",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
    )
    if extra.strip():
        parts.extend(["", "## 额外上下文", extra.strip()])
    return "\n".join(parts)


def _som_stitch_png(api_raw_png: bytes, api_som_png: bytes) -> bytes:
    """单图回退：上下拼接并加标题，兼容只支持单张图片的 vision 后端。"""
    a = Image.open(io.BytesIO(api_raw_png)).convert("RGB")
    b = Image.open(io.BytesIO(api_som_png)).convert("RGB")
    margin = 6
    lab_h = 22
    w = max(a.width, b.width)
    h = lab_h + a.height + margin + lab_h + b.height + margin
    canvas = Image.new("RGB", (w, h), (28, 28, 28))
    dr = ImageDraw.Draw(canvas)
    dr.text((2, 2), "[1] Original (read text)", fill=(255, 210, 120))
    canvas.paste(a, (0, lab_h))
    y2 = lab_h + a.height + margin
    dr.text((2, y2), "[2] SoM (use ~n ids for clicks)", fill=(255, 90, 90))
    canvas.paste(b, (0, y2 + lab_h))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _som_openai_dual_chat(
    api_raw_png: bytes,
    api_som_png: bytes,
    system_txt: str,
    user_txt: str,
    vcfg: dict[str, Any],
) -> str:
    """OpenAI 兼容 Chat Completions：user 含两段 image_url（真·双图）。"""
    api_key = (vcfg.get("openai_api_key") or "").strip()
    base_url = (vcfg.get("openai_base_url") or "").strip().rstrip("/")
    model = (vcfg.get("openai_model") or "").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 openai_api_key（请在 web/config.py 配置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY）"
        )
    if not base_url:
        raise RuntimeError("缺少 openai_base_url（web/config.py）")
    if not model:
        raise RuntimeError("缺少 openai_model（web/config.py）")
    b64a = _encode_image_base64(api_raw_png)
    b64b = _encode_image_base64(api_som_png)
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_txt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_txt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64a}"},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64b}"},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }
    ).encode()
    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except (ssl.SSLError, OSError):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            data = json.loads(resp.read())
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "") or ""
    return ""


def _som_invoke_multimodal(
    api_raw_png: bytes,
    api_som_png: bytes,
    system_txt: str,
    user_txt: str,
) -> str:
    """优先 OpenAI 双图；失败则拼接为单图再走各后端（_call_backend_with_system）。"""
    vcfg = _load_vision_config()
    backend = str(vcfg.get("backend", "auto"))
    errs: list[str] = []

    if backend in ("openai", "auto"):
        try:
            raw = _som_openai_dual_chat(
                api_raw_png, api_som_png, system_txt, user_txt, vcfg
            )
            if raw.strip():
                return raw
        except Exception as e:
            errs.append(f"openai_dual:{e}")

    stitch = _som_stitch_png(api_raw_png, api_som_png)
    if backend == "auto":
        for try_be in ("ollama", "dashscope", "gemini", "openai"):
            try:
                return _call_backend_with_system(
                    try_be, stitch, user_txt, system_txt, vcfg
                )
            except Exception as e:
                errs.append(f"{try_be}:{e}")
        raise RuntimeError("SoM 多模态全部失败: " + "; ".join(errs))
    return _call_backend_with_system(backend, stitch, user_txt, system_txt, vcfg)


def _parse_som_llm_response(raw_text: str) -> dict[str, Any]:
    """解析模型返回的单个 JSON 对象 → 统一 dict（含 action / params）。"""
    text = raw_text.strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    candidates = _extract_json_objects(text)
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "action" not in data:
            continue
        params: dict[str, Any] = dict(data.get("params") or {})
        for k in (
            "element_id",
            "element_confirmed",
            "text",
            "key",
            "keys",
            "seconds",
            "timeout",
            "direction",
            "clicks",
            "x",
            "y",
            "from_api",
            "to_api",
            "from_element_id",
            "to_element_id",
            "query_summary",
            "reason",
            "question",
        ):
            if k not in data or data[k] is None:
                continue
            if k == "element_confirmed":
                params["element_id"] = data[k]
            elif k == "element_id":
                params["element_id"] = data[k]
            else:
                params[k] = data[k]
        # 模型常输出字符串 "null"，不能当 element_id 解析
        _bad = {"null", "none", "nil", ""}
        eidp = params.get("element_id")
        if eidp is not None and str(eidp).strip().lower() in _bad:
            params.pop("element_id", None)
        ec = data.get("element_confirmed")
        if ec is not None and str(ec).strip().lower() in _bad:
            ec = None
        return {
            "action": str(data["action"]).strip().lower(),
            "params": params,
            "reasoning": str(data.get("reasoning", "")),
            "observation": str(data.get("observation", "")),
            "history_check": str(data.get("history_check", "")),
            "element_confirmed": ec,
        }
    return {
        "action": "fail",
        "params": {"reason": raw_text[:400]},
        "reasoning": "JSON 解析失败",
        "observation": "",
        "history_check": "",
    }


def _vision_event_append(entry: dict[str, Any]) -> None:
    """写入内存中的视觉事件流（网页「视觉循环日志」全量展示）；不撑爆 desk_tasks.json。"""
    e = dict(entry)
    e.setdefault("ts_wall", time.strftime("%Y-%m-%d %H:%M:%S"))
    e["ts_mono"] = time.time()
    _state.vision_event_log.append(e)
    if len(_state.vision_event_log) > _VISION_EVENT_LOG_MAX:
        _state.vision_event_log = _state.vision_event_log[-_VISION_EVENT_LOG_MAX:]
    try:
        desk_log.log_vision_event(e)
    except Exception:
        pass


def _som_desk_end_run(success: bool, detail: str) -> None:
    """SoM 运行结束前把完整状态写入当前 run_*.log（与网页面板一致，便于离线审计）。"""
    try:
        desk_log.log(
            "INFO",
            "som",
            f"─── FINAL action_history (n={len(_state.action_history)}) ───",
            data=list(_state.action_history),
        )
        desk_log.log(
            "INFO",
            "som",
            f"─── FINAL vision_event_log (n={len(_state.vision_event_log)}) ───",
            data=list(_state.vision_event_log),
        )
    except Exception:
        pass
    desk_log.end_run(success, detail)


def _exec_res_for_log(res: dict[str, Any]) -> dict[str, Any]:
    """键鼠执行返回值里挑可 JSON 化、便于审计的字段。"""
    keys = (
        "ok",
        "x",
        "y",
        "button",
        "clicks",
        "result",
        "chars",
        "changed",
        "keys",
        "from",
        "to",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in res and res[k] is not None:
            try:
                json.dumps(res[k])
                out[k] = res[k]
            except (TypeError, ValueError):
                out[k] = str(res[k])[:200]
    return out


def _llm_block_som(parsed_llm: dict[str, Any], act: str, params: dict[str, Any], reasoning: str) -> dict[str, Any]:
    return {
        "observation": str(parsed_llm.get("observation", "")),
        "history_check": str(parsed_llm.get("history_check", "")),
        "element_confirmed": parsed_llm.get("element_confirmed"),
        "action": act,
        "params": dict(params),
        "reasoning": reasoning,
    }


def _notify_som_step(payload: dict[str, Any], result: str) -> None:
    if _on_step:
        try:
            _on_step(
                {
                    "step": _state.step_count,
                    "mode": "som",
                    "result": result,
                    **payload,
                }
            )
        except Exception:
            pass


def _human_som_loop(extra_context: str = "") -> None:
    """human/som：ROI → L2 → 双图 API → JSON 动作 → execute_som_action（API→物理）。"""
    hp = config.get_human_policy()
    vp = config.get_vision_policy()
    max_idle = max(8, int(hp.get("max_idle_continues", 80)))
    idle_count = 0
    tag = "human/som"

    # §5 环境感知：首轮注入（仅本段 loop 一次）
    try:
        env_blob = format_env_for_prompt(startup_scan())
        extra_context = env_blob + "\n\n" + (extra_context or "")
        desk_log.log("INFO", tag, "startup_scan 已注入首条上下文")
    except Exception as e:
        desk_log.log("WARN", tag, f"startup_scan 失败（非致命）: {e}")

    desk_log.log("INFO", tag, f"_human_som_loop start goal={_state.goal!r}")

    while _state.step_count < _state.max_steps:
        if _consume_run_abort():
            return
        if interrupt_manager.is_interrupted():
            _state.status = "paused"
            task_state.append_log("warn", f"[{tag}] interrupt_manager：已中断/暂停")
            _som_desk_end_run(False, "interrupt")
            return

        max_calls = int(vp.get("max_calls_per_goal", 0) or 0)
        if max_calls > 0 and _state.vision_api_calls >= max_calls:
            _state.status = "error"
            _state.error = f"多模态调用已达上限 {max_calls}"
            task_state.append_log("error", _state.error)
            _som_desk_end_run(False, _state.error)
            return

        enforce = float(vp.get("enforce_min_interval_sec", 1.0))
        now = time.time()
        if (
            enforce > 0
            and _state.last_vision_api_time > 0
            and (now - _state.last_vision_api_time) < enforce
        ):
            time.sleep(enforce - (now - _state.last_vision_api_time) + 0.05)
            if interrupt_manager.is_interrupted():
                _state.status = "paused"
                return

        if interrupt_manager.is_interrupted():
            _state.status = "paused"
            return

        task_state.append_log("info", f"[{tag}] 截图 ROI + L2 解析…")
        try:
            roi = capture_roi_png()
            parsed = parse_roi_l2(roi.png_bytes)
            dual = prepare_dual_api_payload(roi.image_rgb, parsed.som_image_rgb)
            try:
                desk_log.save_run_som_screenshots(
                    _state.vision_api_calls + 1,
                    dual["api_raw_png"],
                    dual["api_som_png"],
                )
            except Exception:
                pass
        except Exception as e:
            _state.status = "error"
            _state.error = f"ROI/解析失败: {e}"
            task_state.append_log("error", _state.error)
            desk_log.log_exception(tag, "capture/parse")
            _som_desk_end_run(False, _state.error)
            return

        try:
            art = config.write_vision_artifacts(
                roi.png_bytes,
                parsed.som_png_bytes,
                step=_state.step_count,
            )
            _state.vision_raw_latest_path = art.get("vision_raw_latest", "")
            _state.vision_som_latest_path = art.get("vision_som_latest", "")
            _state.vision_artifacts_dir = art.get("vision_dir", "")
        except Exception as e:
            desk_log.log("WARN", tag, f"write_vision_artifacts: {e}")

        ctx = SomFrameContext(
            scale_factor=float(dual["scale_factor"]),
            roi_offset_x=roi.roi_offset_x,
            roi_offset_y=roi.roi_offset_y,
            api_w=int(dual["api_w"]),
            api_h=int(dual["api_h"]),
            roi_label=roi.roi_label,
            elements=list(parsed.elements),
        )

        loop_warn = detect_loop(_state.action_history)
        user_msg = _build_som_user_message(
            goal=_state.goal,
            roi_label=roi.roi_label,
            api_w=ctx.api_w,
            api_h=ctx.api_h,
            elements=parsed.elements,
            scale_factor=ctx.scale_factor,
            history=_state.action_history,
            loop_warn=loop_warn,
            extra=extra_context,
        )

        som_round = _state.vision_api_calls + 1
        try:
            el_rows = [
                {
                    "element_id": el.element_id,
                    "type": el.type,
                    "content": (el.content or "")[:200],
                }
                for el in parsed.elements
            ]
            desk_log.log_som_round_context(
                som_round,
                roi_label=roi.roi_label,
                api_w=ctx.api_w,
                api_h=ctx.api_h,
                scale_factor=ctx.scale_factor,
                loop_warn=loop_warn,
                user_message_text=user_msg,
                elements=el_rows,
            )
        except Exception:
            pass

        try:
            raw = _som_invoke_multimodal(
                dual["api_raw_png"],
                dual["api_som_png"],
                HUMAN_SOM_SYSTEM,
                user_msg,
            )
            _state.last_vision_api_time = time.time()
            _state.vision_api_calls += 1
            desk_log.log("INFO", tag, f"LLM 响应长度={len(raw)}")
            try:
                desk_log.log_som_llm_raw(_state.vision_api_calls, raw)
            except Exception:
                pass
        except Exception as e:
            idle_count += 1
            task_state.append_log("error", f"[{tag}] 多模态调用失败: {e}")
            _vision_event_append(
                {
                    "kind": "llm_http_error",
                    "step": _state.step_count,
                    "roi_label": roi.roi_label,
                    "error": str(e),
                }
            )
            if idle_count > max_idle:
                _state.status = "error"
                _state.error = str(e)
                _som_desk_end_run(False, _state.error)
                return
            time.sleep(1.0)
            continue

        parsed_llm = _parse_som_llm_response(raw)
        act = parsed_llm["action"]
        params = parsed_llm["params"]
        reasoning = parsed_llm.get("reasoning", "")

        if act == "screenshot":
            _vision_event_append(
                {
                    "kind": "som_screenshot",
                    "step": _state.step_count,
                    "roi_label": roi.roi_label,
                    "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                }
            )
            _notify_som_step({**parsed_llm, "action": act}, "OK")
            _save_screenshot(roi.png_bytes)
            idle_count = 0
            if interrupt_manager.is_interrupted():
                _state.status = "paused"
                return
            continue

        if act == "done":
            _vision_event_append(
                {
                    "kind": "terminal",
                    "terminal": "done",
                    "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                }
            )
            _state.status = "done"
            task_state.finish_goal(True, reasoning)
            _notify_som_step(parsed_llm, "OK")
            _som_desk_end_run(True, reasoning)
            return

        if act == "fail":
            _vision_event_append(
                {
                    "kind": "terminal",
                    "terminal": "fail",
                    "error": str(params.get("reason", "fail")),
                    "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                }
            )
            _state.status = "error"
            _state.error = str(params.get("reason", "fail"))
            task_state.finish_goal(False, _state.error)
            _notify_som_step(parsed_llm, "ERROR")
            _som_desk_end_run(False, _state.error)
            return

        if act == "ask_user":
            _vision_event_append(
                {
                    "kind": "terminal",
                    "terminal": "ask_user",
                    "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                }
            )
            _state.status = "paused"
            config.set_paused(True)
            task_state.pause_goal()
            _notify_som_step(parsed_llm, "PAUSED")
            _som_desk_end_run(False, "ask_user")
            return

        elem_id = params.get("element_id") or parsed_llm.get("element_confirmed")

        try:
            exec_res = execute_som_action(act, params, ctx)
        except Exception as e:
            task_state.append_log("error", f"[{tag}] 执行失败: {e}")
            desk_log.log_exception(tag, "execute_som_action")
            snap_err = _som_target_snapshot(parsed.elements, elem_id, params)
            hist = {
                "step": _state.step_count + 1,
                "action": act,
                "params": params,
                "element_id": elem_id,
                "result": f"ERROR:{e}",
                "reasoning": reasoning,
                "timestamp": time.time(),
                "roi_label": roi.roi_label,
                "target_snapshot": snap_err,
                "observation": parsed_llm.get("observation", ""),
                "history_check": parsed_llm.get("history_check", ""),
                "element_confirmed": parsed_llm.get("element_confirmed"),
            }
            _vision_event_append(
                {
                    "kind": "som_exec_error",
                    "step": _state.step_count + 1,
                    "roi_label": roi.roi_label,
                    "error": str(e),
                    "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                    "target_snapshot": snap_err,
                }
            )
            _state.action_history.append(hist)
            _state.step_count += 1
            idle_count += 1
            if idle_count > max_idle:
                _state.status = "error"
                _state.error = "连续失败过多"
                _som_desk_end_run(False, _state.error)
                return
            if interrupt_manager.is_interrupted():
                _state.status = "paused"
                return
            continue

        res_str = str(exec_res.get("result", "OK"))
        if act == "wait_clipboard_change" and not exec_res.get("changed"):
            res_str = "CLIPBOARD_UNCHANGED"

        _state.step_count += 1
        idle_count = 0
        snap = _som_target_snapshot(parsed.elements, elem_id, params)
        hist = {
            "step": _state.step_count,
            "action": act,
            "params": params,
            "element_id": elem_id,
            "result": res_str,
            "reasoning": reasoning,
            "timestamp": time.time(),
            "roi_label": roi.roi_label,
            "target_snapshot": snap,
            "observation": parsed_llm.get("observation", ""),
            "history_check": parsed_llm.get("history_check", ""),
            "element_confirmed": parsed_llm.get("element_confirmed"),
        }
        _state.action_history.append(hist)
        _state.last_action = hist
        _vision_event_append(
            {
                "kind": "som_step",
                "step": _state.step_count,
                "roi_label": roi.roi_label,
                "llm": _llm_block_som(parsed_llm, act, params, reasoning),
                "exec": {
                    "result": res_str,
                    "target_snapshot": snap,
                    "detail": _exec_res_for_log(exec_res),
                },
            }
        )
        task_state.append_log(
            "info",
            f"[{tag}] step#{_state.step_count} {act} | 目标快照: {snap} | result={res_str}",
        )
        desk_log.log("INFO", tag, f"step#{_state.step_count} {act} target={snap} result={res_str}")
        _save_screenshot(roi.png_bytes)
        _notify_som_step(parsed_llm, res_str)

        if interrupt_manager.is_interrupted():
            _state.status = "paused"
            _som_desk_end_run(False, "interrupt_after_step")
            return

    _state.status = "done"
    task_state.finish_goal(False, f"超过最大步数 {_state.max_steps}")
    _som_desk_end_run(False, "max_steps")


def get_state() -> dict[str, Any]:
    return {
        "goal": _state.goal,
        "status": _state.status,
        "exec_mode": _state.exec_mode,
        "human_core": _state.human_core,
        "step_count": _state.step_count,
        "max_steps": _state.max_steps,
        "action_history": _state.action_history[-_VISION_ACTION_HISTORY_API_MAX:],
        "vision_event_log": _state.vision_event_log[-_VISION_EVENT_LOG_MAX:],
        "last_action": _state.last_action,
        "error": _state.error,
        "elapsed_sec": round(time.time() - _state.start_time, 1) if _state.start_time else 0,
        "stable_frame_streak": _state.stable_frame_streak,
        "skill_followup_notified": _state.skill_followup_notified,
        "vision_api_calls": _state.vision_api_calls,
        "frozen_skip_streak": _state.frozen_skip_streak,
        "idle_continue_count": _state.idle_continue_count,
        "vision_raw_latest_path": _state.vision_raw_latest_path,
        "vision_som_latest_path": _state.vision_som_latest_path,
        "vision_artifacts_dir": _state.vision_artifacts_dir,
        "vision_running": (_thread is not None and _thread.is_alive()),
    }


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def start(
    goal: str,
    max_steps: int = 50,
    on_step: Callable[[dict], None] | None = None,
    extra_context: str = "",
    exec_mode: str | None = None,
) -> dict[str, Any]:
    """启动操作循环。

    exec_mode: "auto" / "human" / "skill"，None 则读配置文件。
    """
    global _thread, _on_step

    if is_running():
        return {"ok": False, "error": "操作循环已在运行中"}

    mode = exec_mode or config.get_exec_mode()
    if mode == "program":
        mode = "skill"
    if mode == "vision":
        mode = "human"
    if mode not in ("auto", "human", "skill"):
        mode = "auto"
    if mode != "skill":
        config.assert_automation_allowed()

    _stop.clear()
    _run_aborted.clear()
    reset_esc_stop_for_new_run()
    _on_step = on_step
    _ensure_interrupt_sse_bridge()
    try:
        from backend.web import desk_sse

        desk_sse.ensure_interrupt_forwarder()
    except Exception:
        pass

    hp = config.get_human_policy()
    core = hp.get("human_core", "som")
    if core not in ("llm", "multimodal", "som"):
        core = "som"

    _state.goal = goal
    _state.status = "running"
    _state.exec_mode = mode
    _state.human_core = core
    _state.step_count = 0
    _state.max_steps = max_steps
    _state.action_history = []
    _state.vision_event_log = []
    _state.last_action = {}
    _state.error = ""
    _state.start_time = time.time()
    _state.prev_frame_png = None
    _state.last_vision_api_time = 0.0
    _state.stable_frame_streak = 0
    _state.skill_followup_notified = False
    _state.vision_api_calls = 0
    _state.frozen_skip_streak = 0
    _state.idle_continue_count = 0
    _state.step_budget_warned = False
    _state.vision_raw_latest_path = ""
    _state.vision_som_latest_path = ""
    _state.vision_artifacts_dir = ""

    if mode != "skill":
        if start_esc_listener():
            desk_log.log("INFO", "vision_loop", "pynput ESC 全局监听已启动")
        else:
            desk_log.log(
                "WARN",
                "vision_loop",
                "未安装 pynput：ESC 急停仅依赖 pause 文件 / stop API；可 pip install pynput",
            )

    task_state.set_goal(goal)
    task_state.append_log("info", f"[{mode}] 操作循环启动: {goal}")

    log_path = desk_log.init_run(goal)
    desk_log.log("INFO", "vision_loop", f"start: mode={mode} core={core} goal={goal} max_steps={max_steps}")
    desk_log.log("INFO", "vision_loop", f"日志文件: {log_path}")

    _thread = threading.Thread(
        target=_thread_entry,
        args=(extra_context,),
        daemon=True,
        name="vision_loop",
    )
    _thread.start()
    _push_desk_sse()

    return {"ok": True, "goal": goal, "max_steps": max_steps, "exec_mode": mode, "human_core": core}


def stop() -> dict[str, Any]:
    _stop.set()
    _state.status = "paused"
    task_state.append_log("warn", "[vision] 收到停止信号")
    _push_desk_sse()
    return {"ok": True}


def terminate_run() -> dict[str, Any]:
    """前端「终止任务」：运行中则等循环消费 _run_aborted；已停线程则立即 idle。"""
    _run_aborted.set()
    _stop.set()
    from ..hooks.interrupt_manager import clear_all_interrupt_and_pause

    clear_all_interrupt_and_pause()
    task_state.append_log("warn", "[vision] 已请求终止（terminate_run）")
    if not is_running():
        _run_aborted.clear()
        _stop.clear()
        _state.status = "idle"
        _state.error = ""
        task_state.finish_goal(False, "用户终止")
        try:
            _som_desk_end_run(False, "user_terminate")
        except Exception:
            pass
        _push_desk_sse()
    return {"ok": True}


def resume(
    new_goal: str | None = None,
    extra_context: str | None = None,
) -> dict[str, Any]:
    """恢复 paused 的视觉循环：清除 interrupt_manager 与 pause 文件后重启线程。

    new_goal: 非空则覆盖当前目标并写入 task_state。
    extra_context: 附加到本轮 _loop 的 extra_context（如新指令）。
    """
    from ..hooks.interrupt_manager import clear_all_interrupt_and_pause

    if _state.status != "paused":
        return {"ok": False, "error": "状态不是 paused"}
    if is_running():
        return {"ok": False, "error": "循环仍在运行，请稍后再试"}

    clear_all_interrupt_and_pause()
    _stop.clear()
    _run_aborted.clear()

    ng = (new_goal or "").strip()
    if ng:
        _state.goal = ng
        task_state.set_goal(ng)

    ec = (extra_context or "").strip()
    return start(
        _state.goal,
        max(1, _state.max_steps - _state.step_count),
        _on_step,
        extra_context=ec,
        exec_mode=_state.exec_mode,
    )


# ─── 核心循环 ───

def _minimize_browser_and_show_desktop() -> None:
    """执行前准备：最小化浏览器窗口、切到桌面，确保截图能看到目标而非浏览器。"""
    try:
        from ..input.actions import get_screen_size
        sz = get_screen_size()
        task_state.append_log(
            "info",
            f"[prep] 屏幕分辨率 {sz['width']}x{sz['height']}（DPI-aware），切到桌面…",
        )
    except Exception:
        pass

    try:
        hotkey("win", "d")
        time.sleep(1.5)
        task_state.append_log("info", "[prep] 已发送 Win+D，等待 1.5s 后开始")
    except Exception as e:
        task_state.append_log("warn", f"[prep] 切换桌面失败（非致命）: {e}")
        time.sleep(0.5)


def _loop(extra_context: str = "") -> None:
    """主循环，根据 exec_mode 走不同路径。"""
    mode = _state.exec_mode
    core = _state.human_core
    tag = f"[{mode}]"

    desk_log.log("INFO", "vision_loop", f"_loop 进入: mode={mode} core={core} goal={_state.goal}")

    if mode in ("auto", "human"):
        desk_log.log("INFO", "vision_loop", "准备最小化浏览器切到桌面…")
        _minimize_browser_and_show_desktop()
        desk_log.log("INFO", "vision_loop", "桌面准备完成")

    try:
        if mode in ("auto", "skill"):
            desk_log.log("INFO", "vision_loop", "尝试 skill 链…")
            intent = classify_intent(_state.goal)
            task_state.append_log("info", f"{tag} 意图: {intent}，尝试 skill（程序化）…")
            result = try_programmatic(_state.goal, intent)

            if result.ok:
                _state.step_count = 1
                _state.last_action = {"action": "skill", "params": result.to_dict(), "reasoning": result.detail}
                _state.action_history.append(_state.last_action)
                _state.status = "done"
                task_state.append_log("info", f"{tag} skill 完成: {result.method} — {result.detail}")
                task_state.finish_goal(True, f"[{result.method}] {result.detail}")
                desk_log.end_run(True, f"skill: {result.method}")
                return

            if mode == "skill":
                _state.status = "error"
                _state.error = f"skill 模式无法处理此任务: {result.detail}"
                task_state.append_log("error", f"{tag} {_state.error}")
                task_state.finish_goal(False, _state.error)
                desk_log.end_run(False, _state.error)
                return

            desk_log.log("INFO", "vision_loop", "skill 未覆盖，降级到 human 智能链")
            task_state.append_log("info", f"{tag} skill 未覆盖，降级到 human 智能链…")

        if mode in ("auto", "human"):
            desk_log.log("INFO", "vision_loop", f"进入 human 链，核心={core}")
            if core == "multimodal":
                task_state.append_log(
                    "info",
                    "[human/multimodal] 多模态大脑模式 — 一次规划一批步骤，执行后截图验证",
                )
                _human_multimodal_loop(extra_context)
            elif core == "som":
                task_state.append_log(
                    "info",
                    "[human/som] ROI + L2(YOLO+OCR) + 双图多模态 + element_id 执行",
                )
                _human_som_loop(extra_context)
            else:
                task_state.append_log(
                    "info",
                    "[human/llm] 文本 LLM 大脑 + OCR 定位 + 偶尔调多模态",
                )
                _human_llm_loop(extra_context)
        else:
            desk_log.log("WARN", "vision_loop", f"mode={mode} 没有匹配到任何执行路径")

    except Exception as e:
        _state.status = "error"
        _state.error = str(e)
        tb = traceback.format_exc()
        task_state.append_log("error", f"{tag} 异常: {tb}")
        desk_log.log("ERROR", "vision_loop", f"_loop 顶层异常: {e}")
        desk_log.log_exception("vision_loop", "_loop 顶层异常")
        desk_log.end_run(False, str(e))


def _human_llm_loop(extra_context: str = "") -> None:
    """human/llm 核心：文本 LLM 大脑 + OCR 优先 → 帧差节流 → 局部 ROI 或全图多模态。"""
    hp = config.get_human_policy()
    vp = config.get_vision_policy()
    use_ocr = hp.get("ocr_first", True)
    thr_need = int(hp.get("stable_frames_for_skill", 3))
    max_idle = max(8, int(hp.get("max_idle_continues", 80)))
    step_warn_ratio = float(hp.get("step_warn_ratio", 0.8))
    step_warn_at = max(1, int(_state.max_steps * step_warn_ratio))

    mode_label = "human/llm"
    task_state.append_log(
        "info",
        f"[{mode_label}] 策略摘要: "
        f"ocr={use_ocr} min_api_interval={hp.get('min_api_interval_sec')} "
        f"max_idle_continues={max_idle} vision_enforce={vp.get('enforce_min_interval_sec')} "
        f"max_vision_calls={vp.get('max_calls_per_goal') or '∞'}",
    )

    def _idle_continue(reason: str) -> bool:
        """若超过空闲轮次上限则返回 True（调用方应 break/return）。"""
        _state.idle_continue_count += 1
        if _state.idle_continue_count > max_idle:
            _state.status = "error"
            _state.error = (
                f"human 链连续空转 {max_idle} 次（{reason}），疑似无法推进；"
                "可调大 human_policy.max_idle_continues 或检查画面/OCR/API"
            )
            task_state.append_log("error", f"[human] {_state.error}")
            task_state.finish_goal(False, _state.error)
            return True
        return False

    while _state.step_count < _state.max_steps:
        if _consume_run_abort():
            return
        if _stop.is_set():
            _state.status = "paused"
            task_state.append_log("warn", "[human] 已暂停")
            return

        if config.is_paused():
            task_state.append_log("info", "[human] 检测到暂停标志，等待恢复…")
            while config.is_paused() and not _stop.is_set():
                time.sleep(0.5)
            if _stop.is_set():
                _state.status = "paused"
                return

        task_state.append_log("info", f"[human] 第 {_state.step_count + 1} 步: 截图…")
        try:
            png = grab_screen_png()
        except Exception as e:
            _state.error = f"截图失败: {e}"
            _state.status = "error"
            task_state.append_log("error", _state.error)
            return

        _save_screenshot(png)
        now = time.time()
        fd = None

        if _state.prev_frame_png is not None:
            fd = compare_frames(_state.prev_frame_png, png)
            task_state.append_log("info", f"[{mode_label}] 帧差 {fd.to_log()}")

            if should_throttle_api(fd, hp, now - _state.last_vision_api_time):
                task_state.append_log(
                    "warn",
                    "[human] 节流：全图变化小或局部快变且距上次 API 过近，跳过本轮多模态",
                )
                _state.stable_frame_streak += 1
                if (
                    not _state.skill_followup_notified
                    and _state.stable_frame_streak >= thr_need
                ):
                    if skill_followup.notify_stable_for_skills(
                        _state.goal, _state.stable_frame_streak, thr_need
                    ):
                        _state.skill_followup_notified = True
                _state.prev_frame_png = png
                time.sleep(float(hp.get("throttle_sleep_sec", 0.45)))
                if _idle_continue("节流/跳过多模态"):
                    return
                continue

        _state.stable_frame_streak = 0

        action: ScreenAction | None = None
        source = ""

        if use_ocr:
            action = find_click_by_ocr(png, _state.goal)
            if action is not None:
                source = "ocr"
                task_state.append_log("info", f"[human] OCR 定位成功，未调多模态 API — {action.reasoning}")

        if action is None:
            max_calls = int(vp.get("max_calls_per_goal", 0) or 0)
            if max_calls > 0 and _state.vision_api_calls >= max_calls:
                _state.status = "error"
                _state.error = f"本任务多模态调用已达上限 {max_calls}（可在 desk_automation.json vision.max_calls_per_goal 调整）"
                task_state.append_log("error", f"[human] {_state.error}")
                task_state.finish_goal(False, _state.error)
                return

            fmax = float(vp.get("frozen_diff_max", 0.005))
            if fd is not None and fd.diff_ratio > fmax:
                _state.frozen_skip_streak = 0
            elif fd is not None and fd.diff_ratio <= fmax:
                fskips = int(vp.get("frozen_max_skips", 2))
                if fskips > 0:
                    _state.frozen_skip_streak += 1
                    if _state.frozen_skip_streak <= fskips:
                        task_state.append_log(
                            "warn",
                            "[human/llm] 画面几乎静止，暂缓多模态，短等待后再观察",
                        )
                        _state.prev_frame_png = png
                        time.sleep(float(vp.get("frozen_wait_sec", 0.4)))
                        if _idle_continue("画面静止跳过"):
                            return
                        continue
                _state.frozen_skip_streak = 0

            enforce = float(vp.get("enforce_min_interval_sec", 1.0))
            if (
                enforce > 0
                and _state.last_vision_api_time > 0
                and (now - _state.last_vision_api_time) < enforce
            ):
                wait = enforce - (now - _state.last_vision_api_time) + 0.05
                task_state.append_log(
                    "warn",
                    f"[human/llm] 多模态硬间隔 {enforce}s，等待 {wait:.2f}s",
                )
                _state.prev_frame_png = png
                time.sleep(wait)
                if _idle_continue("多模态硬间隔等待"):
                    return
                continue

            png_for_api = png
            roi_extra = ""
            if fd is not None:
                full_min = float(hp.get("full_screen_diff_min", 0.08))
                if fd.diff_ratio >= full_min:
                    task_state.append_log("info", "[human] 全图变化较大 → 全屏多模态理解")
                elif fd.hotspots:
                    rois = crop_hotspots_to_png(png, fd.hotspots)
                    if rois:
                        comp, layout = compose_rois_for_prompt(rois)
                        png_for_api = comp
                        roi_extra = (
                            "\n\n## 以下为「变化热点」裁剪拼接图\n"
                            + layout
                            + "\n\n请根据任务返回 JSON；若 action 为 click，x/y 必须是**整屏绝对坐标**（根据子图偏移估算中心点）。"
                        )
                        task_state.append_log("info", "[human] 使用中低全图变化 + 局部热点 → 仅局部拼接图调 API（省 token）")
                    else:
                        task_state.append_log("info", "[human] 无有效热点 → 全屏多模态")
                else:
                    task_state.append_log("info", "[human] 帧差无热点 → 全屏多模态")

            task_state.append_log("info", "[human] 调用多模态 API…")
            try:
                action = call_vision_llm(
                    screenshot_png=png_for_api,
                    goal=_state.goal,
                    action_history=_state.action_history,
                    extra_context=extra_context + roi_extra,
                )
                _state.last_vision_api_time = time.time()
                _state.vision_api_calls += 1
                _state.frozen_skip_streak = 0
                source = "api"
            except Exception as e:
                _state.error = f"LLM 调用失败: {e}"
                _state.status = "error"
                task_state.append_log("error", _state.error)
                return

        _state.prev_frame_png = png

        if action is None:
            if _idle_continue("无有效动作(OCR/多模态未返回可执行项)"):
                return
            continue

        _state.last_action = {**action.to_dict(), "source": source}
        _state.step_count += 1
        _state.idle_continue_count = 0
        task_state.append_log("info", f"[human] #{_state.step_count} [{source}] {action.action.value}: {action.reasoning}")

        if _state.step_count >= step_warn_at and not _state.step_budget_warned:
            _state.step_budget_warned = True
            task_state.append_log(
                "warn",
                f"[human] 步数已达 {_state.step_count}/{_state.max_steps}（阈值比例 {step_warn_ratio:.0%}），接近上限",
            )

        if action.action == ActionType.DONE:
            _state.status = "done"
            task_state.append_log("info", "[human] 任务完成!")
            task_state.finish_goal(True, action.reasoning)
            _notify(action)
            return

        if action.action == ActionType.FAIL:
            _state.status = "error"
            _state.error = action.params.get("reason", "未知错误")
            task_state.append_log("error", f"[human] 失败: {_state.error}")
            task_state.finish_goal(False, _state.error)
            _notify(action)
            return

        if action.action == ActionType.ASK_USER:
            _state.status = "paused"
            config.set_paused(True)
            task_state.append_log("warn", f"[human] 需要用户输入: {action.params.get('question', '')}")
            task_state.pause_goal()
            _notify(action)
            return

        try:
            _execute_action(action)
        except Exception as e:
            task_state.append_log("error", f"[human] 执行失败: {e}")
            _state.action_history.append({**action.to_dict(), "result": f"ERROR: {e}"})
            time.sleep(0.5)
            if _idle_continue("键鼠执行失败重试轮"):
                return
            continue

        _state.action_history.append(_state.last_action)
        _notify(action)
        time.sleep(0.3)

    _state.status = "done"
    task_state.append_log("warn", f"[human/llm] 达到最大步数 {_state.max_steps}")
    task_state.finish_goal(False, f"超过最大步数 {_state.max_steps}")


def _human_multimodal_loop(extra_context: str = "") -> None:
    """human/multimodal 核心：多模态模型为大脑。
    一次 API 调用让 LLM 规划一批步骤 → 逐步执行 → 重新截图验证 → 循环。"""
    hp = config.get_human_policy()
    max_idle = max(8, int(hp.get("max_idle_continues", 80)))
    ML = "human/multimodal"

    desk_log.log("INFO", ML, f"_human_multimodal_loop 进入, goal={_state.goal}")
    desk_log.log("INFO", ML, f"max_idle={max_idle} max_steps={_state.max_steps}")
    task_state.append_log("info", f"[{ML}] 批量规划模式启动，每次 API 规划 2~8 步")

    idle_count = 0
    round_num = 0

    while _state.step_count < _state.max_steps:
        if _consume_run_abort():
            return
        round_num += 1
        desk_log.log("INFO", ML, f"===== 第 {round_num} 轮 (已执行 {_state.step_count}/{_state.max_steps} 步) =====")

        if _stop.is_set():
            _state.status = "paused"
            desk_log.log("WARN", ML, "收到停止信号")
            task_state.append_log("warn", f"[{ML}] 已暂停")
            desk_log.end_run(False, "用户停止")
            return

        if config.is_paused():
            desk_log.log("INFO", ML, "暂停标志，等待恢复…")
            task_state.append_log("info", f"[{ML}] 检测到暂停标志，等待恢复…")
            while config.is_paused() and not _stop.is_set():
                time.sleep(0.5)
            if _stop.is_set():
                _state.status = "paused"
                desk_log.end_run(False, "暂停后停止")
                return

        # ── 1. 截图 ──
        desk_log.log("INFO", ML, "第1步: 截图")
        task_state.append_log("info", f"[{ML}] 截图…")
        try:
            png = grab_screen_png()
            desk_log.log("INFO", ML, f"截图成功, size={len(png)} bytes")
        except Exception as e:
            _state.error = f"截图失败: {e}"
            _state.status = "error"
            desk_log.log("ERROR", ML, f"截图失败: {e}")
            desk_log.log_exception(ML, "截图异常")
            task_state.append_log("error", _state.error)
            desk_log.end_run(False, _state.error)
            return

        _save_screenshot(png)

        try:
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(png))
            sw, sh = img.size
            desk_log.log("INFO", ML, f"屏幕分辨率: {sw}x{sh}")
        except Exception as e:
            sw, sh = 1920, 1080
            desk_log.log("WARN", ML, f"无法读取图片尺寸({e})，默认 {sw}x{sh}")

        # ── 2. OCR 扫描 ──
        desk_log.log("INFO", ML, "第2步: OCR 扫描")
        task_state.append_log("info", f"[{ML}] OCR 扫描屏幕文字…")
        ocr_items = []
        try:
            ocr_items = ocr_scan_all(png)
            desk_log.log("INFO", ML, f"OCR 识别到 {len(ocr_items)} 个文字块")
            task_state.append_log("info", f"[{ML}] OCR 识别到 {len(ocr_items)} 个文字块")
        except Exception as e:
            desk_log.log("WARN", ML, f"OCR 扫描失败（非致命）: {e}")
            desk_log.log_exception(ML, "OCR 扫描异常")
            task_state.append_log("warn", f"[{ML}] OCR 扫描失败: {e}")

        # ── 3. 多模态 API 规划 ──
        desk_log.log("INFO", ML, "第3步: 调用多模态 API 规划")
        task_state.append_log("info", f"[{ML}] 调用多模态 API 规划…")
        try:
            batch = call_vision_llm_batch(
                screenshot_png=png,
                goal=_state.goal,
                action_history=_state.action_history,
                extra_context=extra_context,
                screen_width=sw,
                screen_height=sh,
            )
            _state.vision_api_calls += 1
            _state.last_vision_api_time = time.time()
            desk_log.log("INFO", ML, f"API 返回 {len(batch)} 个 action, vision_api_calls={_state.vision_api_calls}")
        except Exception as e:
            _state.error = f"多模态批量 API 失败: {e}"
            _state.status = "error"
            desk_log.log("ERROR", ML, f"API 调用异常: {e}")
            desk_log.log_exception(ML, "call_vision_llm_batch 异常")
            task_state.append_log("error", _state.error)
            desk_log.end_run(False, _state.error)
            return

        # 检查返回的 batch
        if not batch:
            idle_count += 1
            desk_log.log("WARN", ML, f"空规划 (idle_count={idle_count}/{max_idle})")
            if idle_count > max_idle:
                _state.status = "error"
                _state.error = f"[{ML}] 连续 {max_idle} 次空规划"
                desk_log.log("ERROR", ML, _state.error)
                task_state.append_log("error", _state.error)
                task_state.finish_goal(False, _state.error)
                desk_log.end_run(False, _state.error)
                return
            time.sleep(0.5)
            continue

        # 检查第一个 action 是否为 FAIL（API 解析失败的标志）
        if len(batch) == 1 and batch[0].action == ActionType.FAIL:
            reason = batch[0].params.get("reason", "API返回无法解析")
            desk_log.log("ERROR", ML, f"API 返回解析失败: {reason}")
            task_state.append_log("error", f"[{ML}] API 解析失败: {reason}")
            idle_count += 1
            if idle_count > max_idle:
                _state.status = "error"
                _state.error = reason
                task_state.finish_goal(False, _state.error)
                desk_log.end_run(False, _state.error)
                return
            time.sleep(1.0)
            continue

        task_state.append_log("info", f"[{ML}] API 返回 {len(batch)} 步规划")

        # ── 4. 逐步执行 ──
        desk_log.log("INFO", ML, f"第4步: 逐步执行 {len(batch)} 个 action")
        _CLICK_ACTIONS = {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK}

        for i, action in enumerate(batch):
            if _stop.is_set():
                _state.status = "paused"
                desk_log.log("WARN", ML, "批内执行被停止")
                desk_log.end_run(False, "用户停止")
                return
            if _state.step_count >= _state.max_steps:
                desk_log.log("WARN", ML, "达到 max_steps")
                break

            desk_log.log("INFO", ML,
                f"action [{i+1}/{len(batch)}] {action.action.value} "
                f"params={action.params} reasoning={action.reasoning}")

            if action.action == ActionType.DONE:
                _state.status = "done"
                desk_log.log("INFO", ML, "LLM 判定任务完成!")
                task_state.append_log("info", f"[{ML}] 任务完成!")
                task_state.finish_goal(True, action.reasoning)
                _notify(action)
                desk_log.end_run(True, action.reasoning)
                return

            if action.action == ActionType.FAIL:
                _state.status = "error"
                _state.error = action.params.get("reason", "未知错误")
                desk_log.log("ERROR", ML, f"LLM 返回 FAIL: {_state.error}")
                task_state.append_log("error", f"[{ML}] 失败: {_state.error}")
                task_state.finish_goal(False, _state.error)
                _notify(action)
                desk_log.end_run(False, _state.error)
                return

            if action.action == ActionType.ASK_USER:
                _state.status = "paused"
                config.set_paused(True)
                desk_log.log("WARN", ML, f"需要用户输入: {action.params}")
                task_state.append_log("warn", f"[{ML}] 需要用户输入: {action.params.get('question', '')}")
                task_state.pause_goal()
                _notify(action)
                return

            # OCR 坐标校准
            if action.action in _CLICK_ACTIONS:
                target_text = action.params.pop("_target_text", "")
                api_x = int(action.params.get("x", 0))
                api_y = int(action.params.get("y", 0))

                desk_log.log("DEBUG", ML,
                    f"click 校准前: target='{target_text}' api=({api_x},{api_y}) ocr_count={len(ocr_items)}")

                if ocr_items:
                    is_desktop = action.action == ActionType.DOUBLE_CLICK and api_y < (sh - 60)
                    # 桌面空白右键：模型坐标常落在无字区域，就近吸附会点到壁纸 OCR 杂字（如「此」）
                    no_nearest = action.action == ActionType.RIGHT_CLICK
                    cal_x, cal_y, cal_note = ocr_calibrate_click(
                        target_text,
                        api_x,
                        api_y,
                        ocr_items,
                        is_desktop_icon=is_desktop,
                        nearest_when_no_text_match=not no_nearest,
                    )
                    if (cal_x, cal_y) != (api_x, api_y):
                        desk_log.log("INFO", ML,
                            f"OCR校准: ({api_x},{api_y}) → ({cal_x},{cal_y}) | {cal_note}")
                        task_state.append_log("info",
                            f"[{ML}] OCR校准: API({api_x},{api_y}) → OCR({cal_x},{cal_y}) | {cal_note}")
                        action.params["x"] = cal_x
                        action.params["y"] = cal_y
                    else:
                        desk_log.log("DEBUG", ML, f"坐标未变: {cal_note}")
                        task_state.append_log("info", f"[{ML}] 坐标校准: {cal_note}")
                else:
                    desk_log.log("WARN", ML, "无 OCR 数据可用于校准")

            task_state.append_log("info",
                f"[{ML}] 执行 #{_state.step_count+1} 批内 {i+1}/{len(batch)}: "
                f"{action.action.value} — {action.reasoning}")

            try:
                _execute_action(action)
                desk_log.log_action_exec(
                    _state.step_count + 1, i + 1, len(batch), action, "OK")
            except Exception as e:
                desk_log.log("ERROR", ML, f"执行失败: {e}")
                desk_log.log_exception(ML, f"_execute_action 异常 ({action.action.value})")
                task_state.append_log("error", f"[{ML}] 执行失败: {e}")
                _state.action_history.append({**action.to_dict(), "result": f"ERROR: {e}"})
                break

            _state.step_count += 1
            _state.last_action = action.to_dict()
            _state.action_history.append(_state.last_action)
            _notify(action)
            idle_count = 0

            if action.action == ActionType.WAIT:
                pass
            else:
                time.sleep(0.3)

        desk_log.log("INFO", ML, f"本轮 {len(batch)} 步执行完毕，等 0.5s 后重新截图验证")
        time.sleep(0.5)

    _state.status = "done"
    desk_log.log("WARN", ML, f"达到最大步数 {_state.max_steps}")
    task_state.append_log("warn", f"[{ML}] 达到最大步数 {_state.max_steps}")
    task_state.finish_goal(False, f"超过最大步数 {_state.max_steps}")
    desk_log.end_run(False, f"超过最大步数 {_state.max_steps}")


def _execute_action(action: ScreenAction) -> None:
    """执行一个 ScreenAction。"""
    p = action.params
    a = action.action
    desk_log.log("DEBUG", "exec", f"_execute_action: {a.value} params={p}")

    if a == ActionType.CLICK:
        desk_log.log("INFO", "exec", f"click_at({p['x']}, {p['y']})")
        click_at(int(p["x"]), int(p["y"]))
    elif a == ActionType.DOUBLE_CLICK:
        desk_log.log("INFO", "exec", f"double_click({p['x']}, {p['y']})")
        double_click(int(p["x"]), int(p["y"]))
    elif a == ActionType.RIGHT_CLICK:
        desk_log.log("INFO", "exec", f"right_click({p['x']}, {p['y']})")
        right_click(int(p["x"]), int(p["y"]))
    elif a == ActionType.TYPE:
        text = str(p.get("text", ""))
        desk_log.log("INFO", "exec", f"type '{text[:50]}'")
        if any(ord(c) > 127 for c in text):
            type_chinese(text)
        else:
            type_text(text)
    elif a == ActionType.KEY:
        desk_log.log("INFO", "exec", f"press_key('{p['key']}')")
        press_key(str(p["key"]))
    elif a == ActionType.HOTKEY:
        keys = p.get("keys", [])
        desk_log.log("INFO", "exec", f"hotkey({keys})")
        hotkey(*keys)
    elif a == ActionType.SCROLL:
        desk_log.log("INFO", "exec", f"scroll({p.get('clicks', -3)})")
        scroll(int(p.get("clicks", -3)), p.get("x"), p.get("y"))
    elif a == ActionType.DRAG:
        desk_log.log("INFO", "exec", f"drag ({p['start_x']},{p['start_y']}) → ({p['end_x']},{p['end_y']})")
        drag_to(int(p["start_x"]), int(p["start_y"]), int(p["end_x"]), int(p["end_y"]))
    elif a == ActionType.MOVE:
        desk_log.log("INFO", "exec", f"move_to({p['x']}, {p['y']})")
        move_to(int(p["x"]), int(p["y"]))
    elif a == ActionType.WAIT:
        secs = float(p.get("seconds", 1))
        desk_log.log("INFO", "exec", f"wait {secs}s")
        time.sleep(secs)
    else:
        desk_log.log("ERROR", "exec", f"未知动作: {a}")
        raise ValueError(f"未知动作: {a}")
    desk_log.log("DEBUG", "exec", f"_execute_action 完成: {a.value}")


def _save_screenshot(png: bytes) -> None:
    """保存截图到临时目录，供 HTML 面板展示。"""
    tmp = config._config_path().parent / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "vision_latest.png"
    path.write_bytes(png)
    _state.last_screenshot_path = str(path)


def _notify(action: ScreenAction) -> None:
    if _on_step:
        try:
            _on_step({"step": _state.step_count, **action.to_dict()})
        except Exception:
            pass
