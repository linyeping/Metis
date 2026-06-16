# -*- coding: utf-8 -*-
"""
子进程入口：python -m backend.tools.coding.workflow_features.subagents.task_subprocess_worker <in.json> <out.json>

输入 JSON：prompt, subagent_type, workspace_root, session_id（可选）
输出 JSON：ok, result, exit_code
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path


def _route(prompt: str, subagent_type: str, workspace_root: str) -> str:
    st = (subagent_type or "explore").strip().lower().replace("-", "_")
    root = workspace_root or "."

    if st in ("explore", "generalpurpose", "general_purpose"):
        from backend.tools.coding.workflow_features.subagents.delegate_explore import delegate_explore

        return delegate_explore(goal=prompt, root=root, max_depth=3)
    if st in ("shell",):
        from backend.tools.coding.workflow_features.subagents.delegate_shell import delegate_shell

        return delegate_shell(script_description=prompt, cwd=root)
    if st in ("browser", "browser_use"):
        from backend.tools.coding.workflow_features.subagents.delegate_browser import delegate_browser

        return delegate_browser(task=prompt, url="")
    if st in ("best_of_n", "bestofn"):
        from backend.tools.coding.workflow_features.subagents.delegate_best_of_n import delegate_best_of_n

        return delegate_best_of_n(task=prompt, n=3, workspace_root=root)
    if st in ("context_gatherer", "summon_context", "context"):
        from backend.tools.coding.workflow_features.subagents.summon_context_gatherer import summon_context_gatherer

        return summon_context_gatherer(workspace=root, extra_paths=None, max_depth=2)
    if st in ("custom", "custom_agent"):
        from backend.tools.coding.workflow_features.subagents.custom_agent_creator import custom_agent_creator

        return custom_agent_creator(
            name="ad_hoc",
            system_prompt=prompt,
            tools_allow="*",
        )
    return (
        f"❌ 未知 subagent_type: {subagent_type!r}。\n"
        "支持: explore, shell, browser, best_of_n, context_gatherer, custom"
    )


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: task_subprocess_worker <in.json> <out.json>", file=sys.stderr)
        sys.exit(2)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    ok = True
    code = 0
    try:
        spec = json.loads(in_path.read_text(encoding="utf-8"))
        prompt = str(spec.get("prompt", ""))
        st = str(spec.get("subagent_type", "explore"))
        root_raw = str(spec.get("workspace_root", "."))
        allow_out = bool(spec.get("_delegate_outside_effective", False))
        rp, _ = validate_path(
            root_raw,
            must_exist=False,
            allow_create=True,
            allow_paths_outside_workspace=allow_out,
        )
        text = _route(prompt, st, str(rp))
        result = text
    except PathSecurityError as e:
        ok = False
        code = 1
        result = str(e)
    except Exception as e:
        ok = False
        code = 1
        result = f"{e}\n{traceback.format_exc()}"

    out_path.write_text(
        json.dumps({"ok": ok, "result": result, "exit_code": code}, ensure_ascii=False),
        encoding="utf-8",
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
