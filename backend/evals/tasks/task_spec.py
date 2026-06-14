from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


EVALS_ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = EVALS_ROOT / "tasks"
FIXTURES_ROOT = TASKS_ROOT / "fixtures"
SUITES_ROOT = EVALS_ROOT / "suites"


@dataclass(frozen=True)
class EvalTask:
    id: str
    prompt: str
    fixture: str
    checker: str
    max_turns: int = 30
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvalTask":
        return cls(
            id=str(data["id"]),
            prompt=str(data["prompt"]),
            fixture=str(data["fixture"]),
            checker=str(data["checker"]),
            max_turns=int(data.get("max_turns") or 30),
            tags=[str(item) for item in data.get("tags", [])],
        )


@dataclass(frozen=True)
class EvalSuite:
    name: str
    tasks: List[EvalTask]
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def load_suite(name_or_path: str) -> EvalSuite:
    path = _suite_path(name_or_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = [EvalTask.from_dict(item) for item in data.get("tasks", [])]
    return EvalSuite(
        name=str(data.get("name") or path.stem),
        description=str(data.get("description") or ""),
        tasks=tasks,
        metadata={key: value for key, value in data.items() if key not in {"name", "description", "tasks"}},
    )


def fixture_path(task: EvalTask) -> Path:
    path = (FIXTURES_ROOT / task.fixture).resolve(strict=False)
    if not path.is_dir():
        raise FileNotFoundError(f"Eval fixture not found: {path}")
    return path


def checker_path(workspace: Path, task: EvalTask) -> Path:
    path = (workspace / task.checker).resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"Eval checker not found: {path}")
    return path


def _suite_path(name_or_path: str) -> Path:
    raw = Path(str(name_or_path))
    if raw.suffix:
        path = raw
    else:
        path = SUITES_ROOT / f"{name_or_path}.json"
    if not path.is_absolute():
        path = Path.cwd() / path if path.exists() else path
    path = path.resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"Eval suite not found: {path}")
    return path
