"""结构化提问（对齐 C 的 questions[].id / prompt / options）；兼容旧版 questions 为 str 列表。"""
import json
from typing import Any, Dict, List, Optional, Union

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

QuestionItem = Dict[str, Any]


def _normalize_questions(raw: Any) -> List[QuestionItem]:
    """将 str 列表或 dict 列表规范为 C 形状。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []

    out: List[QuestionItem] = []

    if raw and all(isinstance(x, str) for x in raw):
        for i, text in enumerate(raw):
            out.append(
                {
                    "id": f"q{i}",
                    "prompt": text,
                    "options": ["继续", "取消"],
                    "allow_multiple": False,
                    "_legacy_string_list": True,
                }
            )
        return out

    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id", f"q{i}"))
        prompt = q.get("prompt") or q.get("question") or ""
        opts = q.get("options")
        if opts is None:
            opts = []
        if not isinstance(opts, list):
            opts = [str(opts)]
        opts_str = [str(x) for x in opts]
        if len(opts_str) < 2:
            opts_str = (opts_str + ["是", "否"])[:2]
        out.append(
            {
                "id": qid,
                "prompt": str(prompt),
                "options": opts_str,
                "allow_multiple": bool(q.get("allow_multiple", False)),
            }
        )
    return out


@trace_execution
def ask_question(
    title: str,
    questions: Union[List[str], List[Dict[str, Any]], Any],
    *,
    blocking: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    返回 JSON 字符串供宿主/前端渲染。

    questions 支持：
    - C 风格: [{"id":"x","prompt":"...","options":["a","b"],"allow_multiple":false}, ...]
    - 旧版: ["问题1","问题2"]（自动补默认二元选项）
    """
    normalized = _normalize_questions(questions)
    if len(normalized) < 1:
        return json.dumps(
            {
                "type": "ask_question",
                "error": "questions 至少需要 1 项",
                "title": title,
            },
            ensure_ascii=False,
            indent=2,
        )

    payload: Dict[str, Any] = {
        "type": "ask_question",
        "schema_version": 2,
        "title": title,
        "questions": normalized,
        "blocking": blocking,
        "metadata": metadata or {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
