"""Metis Project Profile — stable per-workspace context.

The profile is intentionally separate from ``.metis/memory.json``:
- project-profile.json is stable, human-readable project metadata that can be
  safely loaded into the prompt prefix;
- memory.json is auto-learned working memory and stays tool-readable by default
  to avoid prefix drift.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROFILE_DIR = ".metis"
PROFILE_FILE = "project-profile.json"
PROFILE_SCHEMA_VERSION = 1
_MAX_ITEMS = 12


@dataclass
class ProjectProfile:
    workspace_root: str
    name: str = ""
    project_type: str = ""
    structure: List[str] = field(default_factory=list)
    startup_commands: List[str] = field(default_factory=list)
    test_commands: List[str] = field(default_factory=list)
    common_ports: List[str] = field(default_factory=list)
    design_decisions: List[str] = field(default_factory=list)
    user_preferences: List[str] = field(default_factory=list)
    release_rules: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    schema_version: int = PROFILE_SCHEMA_VERSION

    @property
    def path(self) -> Path:
        return Path(self.workspace_root) / PROFILE_DIR / PROFILE_FILE

    @classmethod
    def load(cls, workspace_root: str) -> "ProjectProfile":
        root = str(Path(workspace_root or ".").resolve())
        path = Path(root) / PROFILE_DIR / PROFILE_FILE
        if not path.exists():
            return cls(workspace_root=root)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fields = set(cls.__dataclass_fields__)
            filtered = {key: value for key, value in data.items() if key in fields and key != "workspace_root"}
            profile = cls(workspace_root=root, **filtered)
            profile._normalize()
            return profile
        except Exception:
            return cls(workspace_root=root)

    def save(self) -> None:
        self._normalize()
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_gitignore(path.parent)
        data = asdict(self)
        data.pop("workspace_root", None)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == text:
                    return
            except OSError:
                pass
        path.write_text(text, encoding="utf-8")

    def merge_missing(self, inferred: "ProjectProfile") -> bool:
        changed = False
        for field_name in (
            "name",
            "project_type",
            "structure",
            "startup_commands",
            "test_commands",
            "common_ports",
            "design_decisions",
            "user_preferences",
            "release_rules",
            "notes",
        ):
            current = getattr(self, field_name)
            incoming = getattr(inferred, field_name)
            if isinstance(current, list):
                merged = _dedupe([*current, *incoming])[:_MAX_ITEMS]
                if merged != current:
                    setattr(self, field_name, merged)
                    changed = True
            elif not current and incoming:
                setattr(self, field_name, incoming)
                changed = True
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            self.schema_version = PROFILE_SCHEMA_VERSION
            changed = True
        return changed

    def to_prompt_block(self) -> str:
        self._normalize()
        if not self._has_content():
            return ""
        lines = ["\n\n---\n[Metis Project Profile]"]
        if self.name:
            lines.append(f"Project: {self.name}")
        if self.project_type:
            lines.append(f"Type: {self.project_type}")
        _append_list(lines, "Structure", self.structure)
        _append_list(lines, "Startup commands", self.startup_commands)
        _append_list(lines, "Test commands", self.test_commands)
        _append_list(lines, "Common ports", self.common_ports)
        _append_list(lines, "Design decisions", self.design_decisions)
        _append_list(lines, "User preferences", self.user_preferences)
        _append_list(lines, "Release/Git rules", self.release_rules)
        _append_list(lines, "Notes", self.notes)
        return "\n".join(lines) + "\n"

    def _has_content(self) -> bool:
        return bool(
            self.name
            or self.project_type
            or self.structure
            or self.startup_commands
            or self.test_commands
            or self.common_ports
            or self.design_decisions
            or self.user_preferences
            or self.release_rules
            or self.notes
        )

    def _normalize(self) -> None:
        self.workspace_root = str(Path(self.workspace_root or ".").resolve())
        self.name = str(self.name or "").strip()
        self.project_type = str(self.project_type or "").strip()
        for field_name in (
            "structure",
            "startup_commands",
            "test_commands",
            "common_ports",
            "design_decisions",
            "user_preferences",
            "release_rules",
            "notes",
        ):
            setattr(self, field_name, _dedupe(getattr(self, field_name))[:_MAX_ITEMS])


def ensure_project_profile(workspace_root: str) -> ProjectProfile:
    root = str(Path(workspace_root or ".").resolve())
    existing = ProjectProfile.load(root)
    inferred = infer_project_profile(root)
    if existing.merge_missing(inferred) or not existing.path.exists():
        existing.save()
    return existing


def infer_project_profile(workspace_root: str) -> ProjectProfile:
    root = Path(workspace_root or ".").resolve()
    profile = ProjectProfile(workspace_root=str(root), name=root.name)
    package = _read_json(root / "desktop" / "package.json")
    pyproject_exists = (root / "pyproject.toml").is_file()

    if package and pyproject_exists:
        profile.project_type = "Python backend + Electron/React TypeScript desktop"
    elif package:
        profile.project_type = "Electron/React TypeScript desktop"
    elif pyproject_exists:
        profile.project_type = "Python project"

    profile.structure = _infer_structure(root)
    scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
    profile.startup_commands = _infer_startup_commands(scripts)
    profile.test_commands = _infer_test_commands(scripts, pyproject_exists)
    profile.common_ports = _infer_ports(scripts)
    profile.design_decisions = _default_design_decisions(root, package)
    profile.user_preferences = _default_user_preferences()
    profile.release_rules = _default_release_rules()
    return profile


def _infer_structure(root: Path) -> List[str]:
    descriptions = {
        "backend": "backend/ — Python backend, runtime loop, tools, web routes, tests",
        "desktop": "desktop/ — Electron shell and React renderer",
        "docs": "docs/ — project documentation and dev-log construction notes",
        ".github": ".github/ — GitHub workflows and repository metadata",
    }
    out: List[str] = []
    for name, description in descriptions.items():
        if (root / name).exists():
            out.append(description)
    if (root / "docs" / "dev-log").exists():
        out.append("docs/dev-log/ — append-only implementation plans and construction logs")
    return out


def _infer_startup_commands(scripts: Dict[str, Any]) -> List[str]:
    commands: List[str] = []
    if "dev" in scripts:
        commands.append("cd desktop && npm run dev")
    if "dev:renderer" in scripts:
        commands.append("cd desktop && npm run dev:renderer")
    if "dev:electron" in scripts:
        commands.append("cd desktop && npm run dev:electron")
    return commands


def _infer_test_commands(scripts: Dict[str, Any], pyproject_exists: bool) -> List[str]:
    commands: List[str] = []
    if pyproject_exists:
        commands.append("python -m pytest")
    if "typecheck" in scripts:
        commands.append("cd desktop && npm run typecheck")
    if "test" in scripts:
        commands.append("cd desktop && npm test")
    if "test:contracts" in scripts:
        commands.append("cd desktop && npm run test:contracts")
    if "smoke:desktop" in scripts:
        commands.append("cd desktop && npm run smoke:desktop")
    return commands


def _infer_ports(scripts: Dict[str, Any]) -> List[str]:
    text = "\n".join(str(value) for value in scripts.values())
    ports = [match.group(1) for match in re.finditer(r"(?:--port\s+|:)(\d{4,5})(?!\d)", text)]
    out = [f"localhost:{port}" for port in _dedupe(ports)]
    if "METIS_DESKTOP_DEV_SERVER" in text and out:
        out[0] = f"{out[0]} (METIS_DESKTOP_DEV_SERVER)"
    return out


def _default_design_decisions(root: Path, package: Dict[str, Any]) -> List[str]:
    decisions: List[str] = []
    if (root / "backend").exists() and (root / "desktop").exists():
        decisions.append("Metis is a local desktop agent: Python backend plus Electron/React frontend.")
    if package:
        decisions.append("Desktop dev server is driven from desktop/package.json scripts; prefer npm scripts over ad-hoc commands.")
    decisions.append("Keep project memory stable in Project Profile; keep volatile run state in .metis/memory.json or audit logs.")
    return decisions


def _default_user_preferences() -> List[str]:
    return [
        "Default to Chinese replies for this project.",
        "Append to dev-log/MVP docs; do not overwrite prior construction notes.",
        "Do not stage or commit local runtime files such as .metis/audit, .metis/cache, .agent_todos.json, or NEWUPDATE.md.",
    ]


def _default_release_rules() -> List[str]:
    return [
        "Do not publish new GitHub release announcements unless explicitly asked.",
        "When requested, attach installer artifacts to the existing release instead of creating a new release post.",
    ]


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _append_list(lines: List[str], title: str, values: Iterable[str]) -> None:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return
    lines.append(f"{title}:")
    for item in items[:_MAX_ITEMS]:
        lines.append(f"- {item}")


def _dedupe(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _ensure_gitignore(metis_dir: Path) -> None:
    entries = [
        "# Metis local project state",
        "memory.json",
        "project-profile.json",
        "audit/",
        "cache/",
        "tool-permissions.jsonl",
    ]
    gitignore = metis_dir / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        lines = existing.splitlines()
        changed = False
        for entry in entries:
            if entry not in lines:
                lines.append(entry)
                changed = True
        if changed or not gitignore.exists():
            gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except Exception:
        pass
