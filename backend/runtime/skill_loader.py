from __future__ import annotations

import fnmatch
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.core.paths import metis_dir, metis_path


BUILTIN_SKILLS_VERSION = 6
SKILL_INDEX_DEFAULT_CONTEXT_WINDOW = 128_000
SKILL_INDEX_MAX_TOKENS_FLOOR = 320
SKILL_INDEX_MAX_TOKENS_CEILING = 2_048


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    title: str
    description: str
    when_to_use: str
    content: str
    body: str
    path: str
    directory: str
    source: str
    enabled: bool = True
    user_invocable: bool = True
    disable_model_invocation: bool = False
    paths: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=list)
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    mtime: float = 0.0

    @property
    def id(self) -> str:
        if self.source == "project":
            return f"project:{self.name}"
        return self.name

    @property
    def invocable(self) -> bool:
        return self.enabled and self.user_invocable

    @property
    def model_invocable(self) -> bool:
        return self.enabled and not self.disable_model_invocation


def builtin_skills_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "builtin_skills"


def global_skills_root() -> Path:
    return metis_dir("skills")


def project_skills_root(workspace_root: str = "") -> Optional[Path]:
    root = str(workspace_root or "").strip()
    if not root:
        return None
    return Path(root).resolve(strict=False) / ".metis" / "skills"


def ensure_builtin_skills_installed() -> None:
    """Copy bundled coding skills into METIS_HOME once per bundled version."""
    source_root = builtin_skills_root()
    if not source_root.is_dir():
        return
    target_root = global_skills_root()
    marker = metis_path("skills", ".builtin-skills.json")
    marker_data = _read_json(marker)
    if int(marker_data.get("version") or 0) >= BUILTIN_SKILLS_VERSION:
        return
    installed: List[str] = []
    refreshed: List[str] = []
    for source_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        if not (source_dir / "SKILL.md").is_file():
            continue
        target_dir = target_root / source_dir.name
        if not target_dir.exists():
            shutil.copytree(source_dir, target_dir)
            installed.append(source_dir.name)
        elif _is_builtin_skill_file(target_dir / "SKILL.md"):
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
            refreshed.append(source_dir.name)
    marker.write_text(
        json.dumps(
            {
                "version": BUILTIN_SKILLS_VERSION,
                "installed": installed,
                "refreshed": refreshed,
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def discover_skills(
    *,
    workspace_root: str = "",
    include_disabled: bool = True,
    include_shadowed: bool = False,
    install_builtins: bool = True,
) -> List[SkillDefinition]:
    if install_builtins:
        ensure_builtin_skills_installed()

    project_root = project_skills_root(workspace_root)
    raw: List[SkillDefinition] = []
    raw.extend(_scan_skill_root(global_skills_root(), source="global"))
    if project_root is not None:
        raw.extend(_scan_skill_root(project_root, source="project"))

    if not include_disabled:
        raw = [skill for skill in raw if skill.enabled]
    if include_shadowed:
        return sorted(raw, key=_skill_sort_key)

    selected: Dict[str, SkillDefinition] = {}
    for skill in sorted(raw, key=_precedence_sort_key):
        selected.setdefault(skill.name, skill)
    return sorted(selected.values(), key=_skill_sort_key)


def build_skills_index(
    *,
    workspace_root: str = "",
    context_window: int = SKILL_INDEX_DEFAULT_CONTEXT_WINDOW,
) -> str:
    token_budget = _skills_index_token_budget(context_window)
    char_budget = token_budget * 4
    skills = [
        skill
        for skill in discover_skills(workspace_root=workspace_root, include_disabled=False)
        if skill.model_invocable and _skill_paths_active(skill, workspace_root)
    ]
    if not skills:
        return ""

    header = (
        "\n\n---\n[可用技能 / Available Skills]\n"
        "需要某项技能时，先调用 load_skill(name) 加载完整 SKILL.md。"
        "下列索引只用于触发判断，技能正文不会常驻上下文，直到被加载。\n"
    )
    lines: List[str] = []
    total = len(header)
    for skill in skills:
        trigger = _truncate_inline(skill.when_to_use, 220)
        description = _truncate_inline(skill.description, 260)
        suffix_parts = []
        if trigger and trigger != description:
            suffix_parts.append(f"触发: {trigger}")
        if skill.paths:
            suffix_parts.append("paths: " + ", ".join(skill.paths[:4]))
        source = "项目" if skill.source == "project" else "内置" if skill.source == "builtin" else "全局"
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
        line = f"- {skill.name} [{source}]: {description}{suffix}\n"
        if total + len(line) > char_budget:
            remaining = len(skills) - len(lines)
            if remaining > 0:
                lines.append(f"- ... 另有 {remaining} 个技能因索引预算被裁剪。\n")
            break
        lines.append(line)
        total += len(line)
    return header + "".join(lines)


def load_skill_content(name: str, *, workspace_root: str = "", arguments: str = "") -> str:
    skill = resolve_skill(name, workspace_root=workspace_root, include_disabled=False)
    if skill is None:
        available = ", ".join(skill.name for skill in discover_skills(workspace_root=workspace_root, include_disabled=False)[:20])
        return f"Error: Skill not found or disabled: {name}. Available skills: {available}"
    return render_skill_for_model(skill, arguments=arguments)


def render_skill_for_model(skill: SkillDefinition, *, arguments: str = "") -> str:
    args = str(arguments or "")
    body = skill.body or skill.content
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", args)
    tool_contract = _skill_tool_contract_block(skill)
    return (
        f"[Loaded Metis skill: {skill.name}]\n"
        f"Source: {skill.source}\n"
        f"Path: {skill.path}\n"
        f"Description: {skill.description}\n"
        + (f"Arguments: {args}\n" if args else "")
        + tool_contract
        + "\n"
        + body.strip()
    ).strip()


def expand_user_skill_command(message: str, *, workspace_root: str = "") -> str:
    raw = str(message or "")
    parsed = parse_skill_command(raw)
    if parsed is None:
        return raw
    name, arguments = parsed
    skill = resolve_skill(name, workspace_root=workspace_root, include_disabled=False)
    if skill is None or not skill.user_invocable:
        return raw
    rendered = render_skill_for_model(skill, arguments=arguments)
    return (
        f"{rendered}\n\n"
        "[Original user request after skill invocation]\n"
        f"{arguments or raw}"
    ).strip()


def parse_skill_command(message: str) -> Optional[Tuple[str, str]]:
    raw = str(message or "").lstrip()
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    first, _, rest = raw[1:].partition(" ")
    name = first.strip()
    if not name or name in {"help", "clear", "compact", "tools", "mode"}:
        return None
    if any(char in name for char in "\\/\n\r\t"):
        return None
    return name, rest.strip()


def resolve_skill(
    name: str,
    *,
    workspace_root: str = "",
    include_disabled: bool = False,
) -> Optional[SkillDefinition]:
    target = _normalize_skill_lookup(name)
    if not target:
        return None
    for skill in discover_skills(
        workspace_root=workspace_root,
        include_disabled=include_disabled,
        include_shadowed=False,
    ):
        if skill.name == target or skill.id == target:
            return skill
    return None


def resolve_skill_by_id(skill_id: str, *, workspace_root: str = "") -> Optional[SkillDefinition]:
    target = str(skill_id or "").strip()
    if not target:
        return None
    for skill in discover_skills(
        workspace_root=workspace_root,
        include_disabled=True,
        include_shadowed=True,
    ):
        if skill.id == target or skill.name == target:
            return skill
    return None


def skill_to_payload(skill: SkillDefinition, *, include_content: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": skill.id,
        "name": skill.title or skill.name,
        "skill_name": skill.name,
        "description": skill.description,
        "when_to_use": skill.when_to_use,
        "path": skill.path,
        "source": skill.source,
        "enabled": skill.enabled,
        "user_invocable": skill.user_invocable,
        "disable_model_invocation": skill.disable_model_invocation,
        "paths": list(skill.paths),
        "allowed_tools": list(skill.allowed_tools),
        "disallowed_tools": list(skill.disallowed_tools),
        "preview": (skill.description or skill.body or skill.content)[:500],
    }
    if include_content:
        payload["content"] = skill.content
        payload["body"] = skill.body
    return payload


def skill_directory_for_id(skill_id: str, *, workspace_root: str = "") -> Optional[Path]:
    skill = resolve_skill_by_id(skill_id, workspace_root=workspace_root)
    if skill is None:
        return None
    return Path(skill.directory)


def skill_disabled_marker(directory: Path) -> Path:
    return directory / ".disabled"


def _scan_skill_root(root: Path, *, source: str) -> List[SkillDefinition]:
    if not root.is_dir():
        return []
    skills: List[SkillDefinition] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_path = directory / "SKILL.md"
        if not skill_path.is_file():
            continue
        skill = _read_skill(skill_path, source=source)
        if skill is not None:
            skills.append(skill)
    return skills


def _read_skill(path: Path, *, source: str) -> Optional[SkillDefinition]:
    try:
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
    except OSError:
        return None
    frontmatter, body = parse_frontmatter(content)
    directory = path.parent
    raw_name = str(frontmatter.get("name") or directory.name).strip()
    name = _normalize_skill_name(raw_name) or _normalize_skill_name(directory.name)
    title = _title_from_body(body, raw_name or directory.name)
    if not name:
        return None
    actual_source = source
    if source == "global" and _bool_value(frontmatter.get("builtin"), False):
        actual_source = "builtin"
    description = _clean_text(frontmatter.get("description")) or _legacy_description(body) or title
    when_to_use = _clean_text(frontmatter.get("when_to_use")) or _clean_text(frontmatter.get("when-to-use"))
    return SkillDefinition(
        name=name,
        title=title,
        description=description,
        when_to_use=when_to_use,
        content=content,
        body=body,
        path=str(path),
        directory=str(directory),
        source=actual_source,
        enabled=not skill_disabled_marker(directory).exists(),
        user_invocable=_bool_value(frontmatter.get("user-invocable", frontmatter.get("user_invocable")), True),
        disable_model_invocation=_bool_value(
            frontmatter.get("disable-model-invocation", frontmatter.get("disable_model_invocation")),
            False,
        ),
        paths=_string_list(frontmatter.get("paths")),
        allowed_tools=_string_list(frontmatter.get("allowed-tools", frontmatter.get("allowed_tools"))),
        disallowed_tools=_string_list(frontmatter.get("disallowed-tools", frontmatter.get("disallowed_tools"))),
        frontmatter=frontmatter,
        mtime=stat.st_mtime,
    )


def _is_builtin_skill_file(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    frontmatter, _body = parse_frontmatter(content)
    return _bool_value(frontmatter.get("builtin"), False)


def _skill_tool_contract_block(skill: SkillDefinition) -> str:
    lines: List[str] = []
    if skill.allowed_tools:
        lines.append("Allowed tools: " + ", ".join(skill.allowed_tools))
    if skill.disallowed_tools:
        lines.append("Disallowed tools: " + ", ".join(skill.disallowed_tools))
    if not lines:
        return ""
    return "".join(f"{line}\n" for line in lines)


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    text = str(content or "")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end_index = -1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index < 0:
        return {}, text
    frontmatter = _parse_simple_yaml(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    if text.endswith("\n"):
        body += "\n"
    return frontmatter, body


def _parse_simple_yaml(lines: Iterable[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_key = ""
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if current_key and stripped.startswith("- "):
            existing = data.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(_parse_scalar(stripped[2:].strip()))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        raw_value = value.strip()
        if not current_key:
            continue
        if not raw_value:
            data[current_key] = []
            continue
        data[current_key] = _parse_scalar(raw_value)
    return data


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if len(raw) >= 2 and raw[0] in {"'", '"'} and raw[-1] == raw[0]:
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    return raw


def _title_from_body(body: str, fallback: str) -> str:
    for line in str(body or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _legacy_description(body: str) -> str:
    paragraph: List[str] = []
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph.append(stripped)
    return " ".join(paragraph).strip()


def _clean_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if "," in raw:
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [raw]
    return []


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _normalize_skill_lookup(name: str) -> str:
    raw = str(name or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    return _normalize_skill_name(raw)


def _normalize_skill_name(name: str) -> str:
    value = str(name or "").strip().lower()
    value = value.replace("_", "-")
    return "".join(char for char in value if char.isalnum() or char in {"-", "."}).strip("-.")


def _precedence_sort_key(skill: SkillDefinition) -> Tuple[int, str]:
    precedence = {"project": 0, "global": 1, "builtin": 1}
    return (precedence.get(skill.source, 9), skill.name)


def _skill_sort_key(skill: SkillDefinition) -> Tuple[int, str]:
    source_order = {"builtin": 0, "global": 1, "project": 2}
    return (source_order.get(skill.source, 9), skill.name)


def _skill_paths_active(skill: SkillDefinition, workspace_root: str = "") -> bool:
    if not skill.paths:
        return True
    root = Path(str(workspace_root or "")).resolve(strict=False) if workspace_root else None
    if root is None or not root.is_dir():
        return False
    for pattern in skill.paths:
        if _workspace_has_match(root, pattern):
            return True
    return False


def _workspace_has_match(root: Path, pattern: str) -> bool:
    cleaned = str(pattern or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return True
    patterns = [cleaned]
    if cleaned.startswith("**/"):
        patterns.append(cleaned[3:])
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
        ]
        rel_dir = Path(current_dir).relative_to(root).as_posix()
        for filename in filenames:
            rel = filename if rel_dir == "." else f"{rel_dir}/{filename}"
            if any(fnmatch.fnmatch(rel, item) or fnmatch.fnmatch(filename, item) for item in patterns):
                return True
    return False


def _skills_index_token_budget(context_window: int) -> int:
    try:
        window = int(context_window)
    except (TypeError, ValueError):
        window = SKILL_INDEX_DEFAULT_CONTEXT_WINDOW
    budget = max(SKILL_INDEX_MAX_TOKENS_FLOOR, window // 100)
    return min(SKILL_INDEX_MAX_TOKENS_CEILING, budget)


def _truncate_inline(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
