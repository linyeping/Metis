# -*- coding: utf-8 -*-
"""详细文件日志 — 按日期/小时/分次运行分级存放。

目录结构（根目录默认 ``<mine/miro>/var/logs``，见 ``config.get_logs_root()``）:
  var/logs/
    2026-03-31/
      18/
        18.04/                          ← 该分钟首次运行；同分钟多次则为 18.04_01、18.04_02 …
          打开gemini_发送你好呀.txt      ← 主文本日志（文件名由目标摘要净化）
          screenshot/
            ROI/                        ← 每轮送多模态前的原图（与 API 坐标系一致）
              round_0001.png
            SOM/                        ← 对应 SoM 标注图
              round_0001.png
          api_….json  ocr_….json        ← 可选侧车记录
      01/
        ...
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _logs_root() -> Path:
    from .. import config

    return config.get_logs_root()

_current_run_dir: Path | None = None
_current_log_file: Path | None = None
_run_start: datetime | None = None
_run_tag: str = ""


def _safe_log_basename(goal: str, max_len: int = 72) -> str:
    """用于主日志 .txt 文件名（不含扩展名）。"""
    raw = (goal or "").strip()[:max_len]
    out = []
    for c in raw:
        if c.isalnum() or c in "._-· ":
            out.append(c)
        else:
            out.append("_")
    s = "".join(out).replace(" ", "_").strip("._")
    return s or "run"


def _allocate_minute_run_dir(hour_dir: Path, now: datetime) -> Path:
    """在 hour 目录下分配本次运行文件夹：优先 HH.MM，占用则 HH.MM_01 …。"""
    base = now.strftime("%H.%M")
    first = hour_dir / base
    if not first.exists():
        return first
    n = 1
    while n < 100:
        cand = hour_dir / f"{base}_{n:02d}"
        if not cand.exists():
            return cand
        n += 1
    return hour_dir / f"{base}_{now.strftime('%H%M%S%f')[:15]}"


def init_run(goal: str) -> Path:
    """每次 vision_loop.start 时调用：创建 logs/日期/时/HH.MM[/序号]/ 与主 txt。"""
    global _current_run_dir, _current_log_file, _run_start, _run_tag

    now = datetime.now()
    _run_start = now
    date_dir = _logs_root() / now.strftime("%Y-%m-%d")
    hour_dir = date_dir / now.strftime("%H")
    hour_dir.mkdir(parents=True, exist_ok=True)

    _current_run_dir = _allocate_minute_run_dir(hour_dir, now)
    _current_run_dir.mkdir(parents=True, exist_ok=False)

    log_base = _safe_log_basename(goal)
    _current_log_file = _current_run_dir / f"{log_base}.txt"

    # 侧车文件名仍用唯一 tag，避免同学分钟多文件混淆
    safe_goal = "".join(c if c.isalnum() or c in "._-" else "_" for c in (goal or "")[:24])
    _run_tag = now.strftime("%H%M%S") + "_" + (safe_goal or "run")

    for parts in (("screenshot",), ("screenshot", "ROI"), ("screenshot", "SOM")):
        try:
            _current_run_dir.joinpath(*parts).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    _write_line("=" * 80)
    _write_line(f"RUN START: {now.isoformat()}")
    _write_line(f"GOAL: {goal}")
    _write_line(f"LOG DIR: {_current_run_dir}")
    _write_line(f"LOG FILE: {_current_log_file}")
    _write_line("=" * 80)

    return _current_log_file


def save_run_som_screenshots(
    round_idx: int,
    raw_png: bytes,
    som_png: bytes,
) -> tuple[str, str] | None:
    """将本轮送多模态的 ROI 原图与 SoM 标注写入当前运行目录（与 api_w×api_h 一致）。"""
    if _current_run_dir is None or not raw_png or not som_png:
        return None
    roi_dir = _current_run_dir / "screenshot" / "ROI"
    som_dir = _current_run_dir / "screenshot" / "SOM"
    try:
        roi_dir.mkdir(parents=True, exist_ok=True)
        som_dir.mkdir(parents=True, exist_ok=True)
        name = f"round_{int(round_idx):04d}.png"
        p_roi = roi_dir / name
        p_som = som_dir / name
        p_roi.write_bytes(raw_png)
        p_som.write_bytes(som_png)
        return (str(p_roi.resolve()), str(p_som.resolve()))
    except OSError:
        return None


def log(level: str, component: str, msg: str, data: dict | list | None = None) -> None:
    """写一条日志。

    level: DEBUG / INFO / WARN / ERROR
    component: vision_loop / screen_reader / ocr / api / ...
    msg: 描述
    data: 可选的结构化数据
    """
    now = datetime.now()
    ts = now.strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level:5s}] [{component}] {msg}"
    _write_line(line)
    if data is not None:
        try:
            pretty = json.dumps(data, ensure_ascii=False, indent=2, default=str)
            for dl in pretty.split("\n"):
                _write_line(f"  | {dl}")
        except Exception:
            _write_line(f"  | (data dump failed): {repr(data)[:500]}")


def log_api_call(
    backend: str,
    prompt: str,
    system_prompt: str,
    raw_response: str,
    elapsed_sec: float,
    error: str = "",
) -> None:
    """记录一次多模态 API 调用的完整请求/响应。"""
    now = datetime.now()
    ts = now.strftime("%H%M%S%f")[:10]

    log("INFO", "api", f"API call [{backend}] elapsed={elapsed_sec:.2f}s err={error or 'none'}")

    if _current_run_dir is None:
        return

    api_file = _current_run_dir / f"api_{ts}.json"
    record = {
        "timestamp": now.isoformat(),
        "backend": backend,
        "elapsed_sec": round(elapsed_sec, 3),
        "system_prompt_len": len(system_prompt),
        "user_prompt": prompt[:3000],
        "raw_response": raw_response[:5000],
        "error": error,
    }
    try:
        api_file.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        _write_line(f"  | [WARN] 写 API 日志文件失败: {e}")


def log_ocr_results(items: list, elapsed_sec: float) -> None:
    """记录一次 OCR 扫描的全部结果。"""
    log("INFO", "ocr", f"OCR scan: {len(items)} items, elapsed={elapsed_sec:.2f}s")
    if not items:
        return

    now = datetime.now()
    ts = now.strftime("%H%M%S%f")[:10]
    if _current_run_dir is None:
        return

    ocr_file = _current_run_dir / f"ocr_{ts}.json"
    records = []
    for it in items:
        records.append({
            "text": getattr(it, "text", str(it)),
            "x": getattr(it, "x", 0),
            "y": getattr(it, "y", 0),
            "score": getattr(it, "score", 0),
            "engine": getattr(it, "engine", "?"),
        })
    try:
        ocr_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def log_batch_plan(batch: list, raw_text: str = "") -> None:
    """记录 API 返回的批量操作计划。"""
    log("INFO", "plan", f"Batch plan: {len(batch)} actions")
    for i, act in enumerate(batch):
        action_val = act.action.value if hasattr(act, "action") else str(act)
        params = act.params if hasattr(act, "params") else {}
        reasoning = act.reasoning if hasattr(act, "reasoning") else ""
        log("DEBUG", "plan", f"  [{i+1}] {action_val} params={params} reason={reasoning}")


def log_calibration(
    target_text: str,
    api_x: int, api_y: int,
    cal_x: int, cal_y: int,
    note: str,
) -> None:
    """记录一次坐标校准。"""
    changed = (api_x != cal_x or api_y != cal_y)
    level = "INFO" if changed else "DEBUG"
    log(level, "calibrate",
        f"target='{target_text}' API({api_x},{api_y}) → OCR({cal_x},{cal_y}) | {note}")


def log_action_exec(step: int, batch_idx: int, batch_total: int, action, result: str = "OK") -> None:
    """记录一个动作的执行。"""
    action_val = action.action.value if hasattr(action, "action") else str(action)
    params = action.params if hasattr(action, "params") else {}
    log("INFO", "exec", f"Step #{step} [{batch_idx}/{batch_total}] {action_val} {params} → {result}")


def log_vision_event(event: dict[str, Any]) -> None:
    """将 vision_event_log 等价结构写入当前 run 的 .txt（与网页「视觉循环日志」一致，便于事后对照）。"""
    if _current_log_file is None:
        return
    kind = event.get("kind", "?")
    step = event.get("step", "-")
    _write_line("")
    _write_line(f"▼▼▼ VISION_EVENT kind={kind} step={step} ▼▼▼")
    try:
        pretty = json.dumps(event, ensure_ascii=False, indent=2, default=str)
        _write_line(pretty)
    except Exception as ex:
        _write_line(f"(vision_event json failed: {ex})")
        _write_line(repr(event)[:4000])
    _write_line("▲▲▲ END VISION_EVENT ▲▲▲")
    _write_line("")


def log_som_llm_raw(som_round: int, raw_text: str, max_chars: int = 100000) -> None:
    """SoM 多模态返回的原始文本整段写入 .log（过长截断）。"""
    if _current_log_file is None:
        return
    _write_line("")
    _write_line(
        f"%%%% SOM_LLM_RAW som_round={som_round} chars={len(raw_text)} (max_log={max_chars}) %%%%"
    )
    body = raw_text if len(raw_text) <= max_chars else raw_text[:max_chars] + "\n\n... [TRUNCATED] ..."
    _write_line(body)
    _write_line("%%%% END SOM_LLM_RAW %%%%")
    _write_line("")


def log_som_round_context(
    som_round: int,
    *,
    roi_label: str,
    api_w: int,
    api_h: int,
    scale_factor: float,
    loop_warn: str,
    user_message_text: str,
    elements: list[dict[str, Any]],
    user_text_max: int = 24000,
    elements_max: int = 120,
) -> None:
    """每轮调用多模态前：ROI、死循环提示、User 文本（无图）、SoM 元素摘要 → 写入 .log。"""
    if _current_log_file is None:
        return
    um = user_message_text
    if len(um) > user_text_max:
        um = um[:user_text_max] + "\n\n... [user_message TRUNCATED] ..."
    els = elements[:elements_max]
    payload: dict[str, Any] = {
        "som_round": som_round,
        "roi_label": roi_label,
        "api_w": api_w,
        "api_h": api_h,
        "scale_factor": scale_factor,
        "loop_warn": loop_warn,
        "user_message_text": um,
        "elements_preview": els,
        "elements_total": len(elements),
    }
    _write_line("")
    _write_line(f"▼▼▼ SOM_ROUND_INPUT som_round={som_round} ▼▼▼")
    try:
        _write_line(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    except Exception as ex:
        _write_line(f"(dump failed {ex})")
    _write_line("▲▲▲ END SOM_ROUND_INPUT ▲▲▲")
    _write_line("")


def log_exception(component: str, msg: str = "") -> None:
    """记录异常（含 traceback）。"""
    tb = traceback.format_exc()
    log("ERROR", component, f"{msg}\n{tb}")


def end_run(success: bool, detail: str = "") -> None:
    """运行结束。"""
    elapsed = 0.0
    if _run_start:
        elapsed = (datetime.now() - _run_start).total_seconds()
    status = "SUCCESS" if success else "FAILED"
    _write_line("=" * 80)
    _write_line(f"RUN END: {status} elapsed={elapsed:.1f}s detail={detail}")
    _write_line("=" * 80)


def _write_line(line: str) -> None:
    if _current_log_file is None:
        return
    with _lock:
        try:
            with open(_current_log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
