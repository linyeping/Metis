"""将仓库内工作流指南注入 system（可选；默认关闭以省 token）。"""
from __future__ import annotations

import os
from typing import Optional


def workflow_guidelines_enabled() -> bool:
    return os.environ.get("MIRO_CONTEXT_WORKFLOW_HINT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def workflow_guidelines_block(workspace_root: Optional[str]) -> str:
    if not workspace_root or not workflow_guidelines_enabled():
        return ""
    try:
        max_c = int(os.environ.get("MIRO_WORKFLOW_GUIDELINES_MAX_CHARS", "4000"))
        max_c = max(500, min(max_c, 32000))
    except ValueError:
        max_c = 4000
    from backend.tools.coding.workflow_features.special_powers.load_workflow_guidelines import (
        load_workflow_guidelines,
    )

    text = load_workflow_guidelines(workspace_root, max_chars=max_c)
    if not text.strip() or text.strip().startswith("📭"):
        return ""
    return "\n\n---\n[Miro workflow guidelines]\n" + text.strip() + "\n"
