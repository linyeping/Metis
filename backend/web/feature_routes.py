"""Memory, skills, cron, and metis-status Blueprint."""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List

from flask import Blueprint, abort, jsonify, request

from backend.core.paths import legacy_miro_path, metis_path
from backend.runtime.skill_loader import (
    discover_skills,
    resolve_skill_by_id,
    skill_directory_for_id,
    skill_to_payload,
)
from backend.web.helpers import (
    active_workspace_root,
    get_state,
    memory_paths_payload,
    open_directory,
    read_text,
    request_client_is_loopback,
    safe_skill_slug,
    skill_title,
    unique_skill_dir,
    write_text,
)
from backend.web.llm_state import env_bool

feature_bp = Blueprint("feature", __name__)


# ---------------------------------------------------------------------------
# METIS.md / MIRO.md status
# ---------------------------------------------------------------------------

def _metis_md_status_payload() -> Dict[str, Any]:
    root = active_workspace_root()
    project_path = os.path.join(root, "METIS.md")
    legacy_project_path = os.path.join(root, "MIRO.md")
    global_path = str(metis_path("METIS.md"))
    legacy_global_path = str(legacy_miro_path("MIRO.md"))
    project_active_path = project_path if os.path.isfile(project_path) else legacy_project_path
    global_active_path = global_path if os.path.isfile(global_path) else legacy_global_path
    return {
        "project": os.path.isfile(project_active_path),
        "global": os.path.isfile(global_active_path),
        "project_path": project_active_path if os.path.isfile(project_active_path) else None,
        "global_path": global_active_path if os.path.isfile(global_active_path) else None,
        "project_legacy": not os.path.isfile(project_path) and os.path.isfile(legacy_project_path),
        "global_legacy": not os.path.isfile(global_path) and os.path.isfile(legacy_global_path),
    }


@feature_bp.route("/metis-md/status", methods=["GET"])
@feature_bp.route("/miro-md/status", methods=["GET"])
def metis_md_status() -> Any:
    return jsonify(_metis_md_status_payload())


@feature_bp.route("/metis/status", methods=["GET"])
@feature_bp.route("/miro/status", methods=["GET"])
def metis_status() -> Any:
    state = get_state()
    root = active_workspace_root()
    metis_dir = os.path.join(root, ".metis")
    legacy_dir = os.path.join(root, ".miro")
    permissions_path = os.path.join(metis_dir, "permissions.json")
    hooks_path = os.path.join(metis_dir, "hooks.json")
    return jsonify(
        {
            "memory": _metis_md_status_payload(),
            "config_dir": metis_dir if os.path.isdir(metis_dir) else None,
            "legacy_config_dir": legacy_dir if os.path.isdir(legacy_dir) else None,
            "permissions": os.path.isfile(permissions_path)
            or os.path.isfile(os.path.join(legacy_dir, "permissions.json")),
            "hooks": os.path.isfile(hooks_path)
            or os.path.isfile(os.path.join(legacy_dir, "hooks.json")),
            "compact": state.last_compact_status or {"running": False},
        }
    )


# ---------------------------------------------------------------------------
# Memory routes
# ---------------------------------------------------------------------------

@feature_bp.route("/memory", methods=["GET"])
def get_memory() -> Any:
    paths = memory_paths_payload()
    return jsonify(
        {
            **paths,
            "global_content": read_text(paths["global_path"]),
            "project_content": read_text(paths["project_path"]),
            "auto_memory": env_bool("METIS_AUTO_MEMORY", "MIRO_AUTO_MEMORY", True),
            "auto_skills": env_bool("METIS_AUTO_SKILLS", "MIRO_AUTO_SKILLS", True),
        }
    )


@feature_bp.route("/memory", methods=["POST"])
def save_memory() -> Any:
    data = request.get_json(silent=True) or {}
    paths = memory_paths_payload()
    if "global_content" in data:
        write_text(paths["global_path"], str(data.get("global_content") or ""))
    if "project_content" in data:
        write_text(paths["project_path"], str(data.get("project_content") or ""))
    return jsonify({"ok": True, **paths})


# ---------------------------------------------------------------------------
# Skills routes
# ---------------------------------------------------------------------------

def _skill_disabled_marker(sd: str) -> str:
    return os.path.join(os.path.realpath(sd), ".disabled")


def _skill_payload(sid: str, include_content: bool = False) -> Dict[str, Any]:
    skill = resolve_skill_by_id(sid, workspace_root=active_workspace_root())
    if skill is None:
        abort(404, description="Skill not found")
    return skill_to_payload(skill, include_content=include_content)


def _list_local_skills() -> List[Dict[str, Any]]:
    return [
        skill_to_payload(skill)
        for skill in discover_skills(
            workspace_root=active_workspace_root(),
            include_disabled=True,
            include_shadowed=True,
        )
    ]


def _skill_dir_for_id(sid: str) -> str:
    if not sid or sid in {".", ".."} or "/" in sid or "\\" in sid:
        abort(400, description="Invalid skill id")
    target = skill_directory_for_id(sid, workspace_root=active_workspace_root())
    if target is None or not os.path.isfile(os.path.join(str(target), "SKILL.md")):
        abort(404, description="Skill not found")
    return str(target)


def _skill_id_from_dir(sd: str) -> str:
    return os.path.basename(os.path.realpath(sd))


def _import_skill_from_path(source: str) -> Dict[str, Any]:
    source_path = os.path.realpath(os.path.expanduser(source.strip()))
    if os.path.isfile(source_path):
        if os.path.basename(source_path).lower() != "skill.md":
            abort(400, description="Import file must be SKILL.md")
        source_dir = os.path.dirname(source_path)
    else:
        source_dir = source_path

    source_skill_path = os.path.join(source_dir, "SKILL.md")
    if not os.path.isfile(source_skill_path):
        abort(400, description="Selected path does not contain SKILL.md")

    content = read_text(source_skill_path)
    slug = safe_skill_slug(skill_title(content, os.path.basename(source_dir)))
    dest_dir = unique_skill_dir(slug)
    shutil.copytree(source_dir, dest_dir, ignore=shutil.ignore_patterns(".disabled"))
    return _skill_payload(_skill_id_from_dir(dest_dir), include_content=True)


@feature_bp.route("/skills", methods=["GET"])
def list_skills() -> Any:
    skills = _list_local_skills()
    return jsonify(
        {
            "skills": skills,
            "groups": {
                "builtin": [skill for skill in skills if skill.get("source") == "builtin"],
                "global": [skill for skill in skills if skill.get("source") == "global"],
                "project": [skill for skill in skills if skill.get("source") == "project"],
            },
        }
    )


@feature_bp.route("/skills/import", methods=["POST"])
def import_skill() -> Any:
    data = request.get_json(silent=True) or {}
    source = str(data.get("path") or "").strip()
    if not source:
        abort(400, description="Missing import path")
    return jsonify({"ok": True, "skill": _import_skill_from_path(source)})


@feature_bp.route("/skills/<path:skill_id>", methods=["GET"])
def get_skill(skill_id: str) -> Any:
    return jsonify(_skill_payload(skill_id, include_content=True))


@feature_bp.route("/skills/<path:skill_id>", methods=["POST"])
def save_skill(skill_id: str) -> Any:
    data = request.get_json(silent=True) or {}
    if "content" not in data:
        abort(400, description="Missing skill content")
    skill_dir = _skill_dir_for_id(skill_id)
    skill_path = os.path.join(skill_dir, "SKILL.md")
    write_text(skill_path, str(data.get("content") or ""))
    return jsonify({"ok": True, "skill": _skill_payload(skill_id, include_content=True)})


@feature_bp.route("/skills/<path:skill_id>/toggle", methods=["POST"])
def toggle_skill(skill_id: str) -> Any:
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    skill_dir = _skill_dir_for_id(skill_id)
    marker = _skill_disabled_marker(skill_dir)
    if enabled:
        if os.path.exists(marker):
            os.remove(marker)
    else:
        write_text(marker, "disabled\n")
    return jsonify({"ok": True, "skill": _skill_payload(skill_id, include_content=True)})


@feature_bp.route("/skills/<path:skill_id>/open-folder", methods=["POST"])
def open_skill_folder(skill_id: str) -> Any:
    skill_dir = _skill_dir_for_id(skill_id)
    ok = open_directory(skill_dir)
    return jsonify({"ok": ok, "path": skill_dir})


@feature_bp.route("/skills/<path:skill_id>", methods=["DELETE"])
def delete_skill(skill_id: str) -> Any:
    skill_dir = _skill_dir_for_id(skill_id)
    shutil.rmtree(skill_dir)
    return jsonify({"ok": True, "id": skill_id, "path": skill_dir})


# ---------------------------------------------------------------------------
# Cron / scheduled tasks routes
# ---------------------------------------------------------------------------

@feature_bp.route("/cron", methods=["GET"])
def cron_list() -> Any:
    from backend.web.scheduler import list_tasks
    return jsonify({"tasks": list_tasks()})


@feature_bp.route("/cron", methods=["POST"])
def cron_create() -> Any:
    from backend.web.scheduler import create_task
    data = request.get_json(silent=True) or {}
    if not str(data.get("prompt") or "").strip():
        return jsonify({"error": "prompt required"}), 400
    return jsonify(create_task(data))


@feature_bp.route("/cron/<task_id>", methods=["POST"])
def cron_update(task_id: str) -> Any:
    from backend.web.scheduler import update_task
    data = request.get_json(silent=True) or {}
    try:
        task = update_task(task_id, data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


@feature_bp.route("/cron/<task_id>", methods=["DELETE"])
def cron_delete(task_id: str) -> Any:
    from backend.web.scheduler import delete_task
    if not delete_task(task_id):
        return jsonify({"error": "task not found"}), 404
    return jsonify({"ok": True})


@feature_bp.route("/cron/<task_id>/toggle", methods=["POST"])
def cron_toggle(task_id: str) -> Any:
    from backend.web.scheduler import toggle_task
    task = toggle_task(task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


@feature_bp.route("/cron/<task_id>/run", methods=["POST"])
def cron_run(task_id: str) -> Any:
    from backend.web.scheduler import run_task_now
    result = run_task_now(task_id)
    if result is None:
        return jsonify({"error": "task not found"}), 404
    return jsonify(result)


# ---------------------------------------------------------------------------
# Metis/Miro init
# ---------------------------------------------------------------------------

@feature_bp.route("/metis/init", methods=["POST"])
@feature_bp.route("/miro/init", methods=["POST"])
def init_metis_config() -> Any:
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden"}), 403

    metis_dir = os.path.join(active_workspace_root(), ".metis")
    os.makedirs(metis_dir, exist_ok=True)
    created: List[str] = []

    perms_path = os.path.join(metis_dir, "permissions.json")
    if not os.path.exists(perms_path):
        with open(perms_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "rules": [
                        {"tool": "read_file", "action": "allow"},
                        {"tool": "read_multiple_files", "action": "allow"},
                        {"tool": "list_directory", "action": "allow"},
                        {"tool": "grep_search", "action": "allow"},
                        {"tool": "glob_search", "action": "allow"},
                        {"tool": "search_in_files", "action": "allow"},
                        {"tool": "read_terminal_state", "action": "allow"},
                        {"tool": "generate_repo_map", "action": "allow"},
                        {"tool": "git_diff", "action": "allow"},
                        {
                            "tool": "execute_bash_command",
                            "args_match": {"command": "git status*"},
                            "action": "allow",
                        },
                    ]
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        created.append(perms_path)

    hooks_path = os.path.join(metis_dir, "hooks.json")
    if not os.path.exists(hooks_path):
        with open(hooks_path, "w", encoding="utf-8") as handle:
            json.dump({"hooks": []}, handle, ensure_ascii=False, indent=2)
        created.append(hooks_path)

    return jsonify({"ok": True, "path": metis_dir, "created": created})
