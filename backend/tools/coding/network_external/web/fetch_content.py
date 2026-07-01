from __future__ import annotations

import os
import re
import subprocess
from typing import Any
from urllib.parse import quote, urlparse

from backend.core.paths import metis_dir
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.research_jobs import clean_user_source_url, save_research_activity_job
from backend.tools.coding.network_external.web.search_broker import metis_page_read, research_payload_comment


def metis_fetch_content(
    url: str,
    max_chars: int = 8000,
    prefer_jina: bool = True,
) -> dict[str, Any]:
    target = str(url or "").strip()
    github_info = _github_url_info(target)
    source_type = _initial_fetch_source_type(target, github_info)
    activity = _fetch_activity_payload(
        {
            "ok": False,
            "source_type": source_type,
            "url": target,
            "final_url": target,
            "title": target,
            "attempts": [],
            "status": "queued",
            "chars": 0,
        },
        phases=_fetch_running_phases(
            source_type,
            fetch_status="running",
            fallback_status="queued" if prefer_jina and source_type == "web_page" else "skipped",
        ),
        source_status="search_result",
    )
    _persist_fetch_activity_payload(activity, status="running")
    fetch_job_id = str(activity.get("job_id") or "")

    if github_info and github_info["type"] in {"repo", "tree", "commit"}:
        result = _fetch_github_repository(github_info, max_chars=max_chars)
        result["research_activity"] = _fetch_activity_payload(result)
        if fetch_job_id:
            result["research_activity"]["job_id"] = fetch_job_id
        _persist_fetch_activity(result)
        return result

    normalized = _github_raw_url(target) or target
    source_type = "github_blob" if normalized != target else "web_page"
    attempts: list[dict[str, Any]] = []

    primary = metis_page_read(normalized, max_chars=max_chars)
    attempts.append(_attempt_summary("requests", primary))
    best = primary

    if source_type != "github_blob" and prefer_jina and _needs_reader_fallback(primary):
        activity = _fetch_activity_payload(
            {
                "ok": bool(primary.get("ok")),
                "source_type": source_type,
                "url": target,
                "final_url": primary.get("final_url") or primary.get("url") or normalized,
                "title": primary.get("title") or target,
                "attempts": attempts,
                "status": primary.get("status") or ("ok" if primary.get("ok") else "error"),
                "chars": primary.get("chars") or len(str(primary.get("text") or "")),
                "error": primary.get("error") or "",
            },
            phases=_fetch_running_phases(
                source_type,
                fetch_status="partial" if primary.get("status") == "partial" else "complete" if primary.get("ok") else "error",
                fallback_status="running",
            ),
            source_status="search_result",
        )
        if fetch_job_id:
            activity["job_id"] = fetch_job_id
        _persist_fetch_activity_payload(activity, status="running")
        reader_url = _jina_reader_url(target)
        reader = metis_page_read(reader_url, max_chars=max_chars)
        reader["reader_url"] = reader_url
        attempts.append(_attempt_summary("jina_reader", reader))
        if _is_better_read(reader, primary):
            best = reader

    visible_final_url = _fetch_visible_final_url(target, normalized, primary, best)
    result: dict[str, Any] = {
        "ok": bool(best.get("ok")),
        "source_type": source_type,
        "url": clean_user_source_url(target) or target,
        "fetched_url": normalized,
        "provider": "jina_reader" if best is not primary else "requests",
        "fallback_used": best is not primary,
        "attempts": attempts,
        "status": best.get("status") or ("ok" if best.get("ok") else "error"),
        "final_url": visible_final_url,
        "title": _source_title(best.get("title"), visible_final_url),
        "content_type": best.get("content_type") or "",
        "text": best.get("text") or "",
        "chars": best.get("chars") or len(str(best.get("text") or "")),
        "truncated": bool(best.get("truncated")),
    }
    if not best.get("ok"):
        result["error"] = best.get("error") or "unknown error"
    result["research_activity"] = _fetch_activity_payload(result)
    if fetch_job_id:
        result["research_activity"]["job_id"] = fetch_job_id
    _persist_fetch_activity(result)
    return result


@trace_execution
def fetch_content(url: str, max_chars: int = 8000, prefer_jina: bool = True) -> str:
    return format_fetch_content_response(metis_fetch_content(url=url, max_chars=max_chars, prefer_jina=prefer_jina))


def format_fetch_content_response(result: dict[str, Any]) -> str:
    payload = result.get("research_activity") if isinstance(result.get("research_activity"), dict) else None
    head = research_payload_comment(payload)
    if not result.get("ok"):
        body = (
            f"❌ fetch_content 失败: {result.get('url')}\n"
            f"状态: {result.get('status')}\n"
            f"错误: {result.get('error') or 'unknown error'}\n"
            f"尝试: {_format_attempts(result.get('attempts'))}"
        )
        return f"{head}\n{body}" if head else body

    lines = [
        f"=== Fetch Content: {result.get('url')} ===",
        f"Provider: {result.get('provider')}",
        f"Source type: {result.get('source_type')}",
        f"Final URL: {result.get('final_url')}",
        f"Content type: {result.get('content_type') or 'unknown'}",
        f"Status: {result.get('status')}",
        f"Length: {result.get('chars')} chars{' (truncated)' if result.get('truncated') else ''}",
        f"Attempts: {_format_attempts(result.get('attempts'))}",
        "",
        str(result.get("text") or "(empty extracted text)").strip(),
    ]
    body = "\n".join(lines)
    return f"{head}\n{body}" if head else body


def _github_raw_url(url: str) -> str:
    info = _github_url_info(url)
    if info and info["type"] == "blob":
        owner, repo, ref, rest = info["owner"], info["repo"], info["ref"], info["path"]
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"
    return ""


def _github_url_info(url: str) -> dict[str, str] | None:
    parsed = urlparse(str(url or ""))
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not _safe_github_part(owner) or not _safe_github_part(repo):
        return None
    if len(parts) >= 4 and parts[2] in {"blob", "tree"}:
        ref, rest = _split_github_ref_and_path(parts[3:])
        return {"type": parts[2], "owner": owner, "repo": repo, "ref": ref, "path": rest, "url": url}
    if len(parts) >= 4 and parts[2] == "commit":
        return {"type": "commit", "owner": owner, "repo": repo, "ref": parts[3], "path": "", "url": url}
    return {"type": "repo", "owner": owner, "repo": repo, "ref": "", "path": "", "url": url}


def _initial_fetch_source_type(target: str, github_info: dict[str, str] | None) -> str:
    if github_info:
        return f"github_{github_info['type']}"
    return "github_blob" if _github_raw_url(target) else "web_page"


def _split_github_ref_and_path(parts: list[str]) -> tuple[str, str]:
    if not parts:
        return "", ""
    # GitHub branch names may contain slashes. Prefer common branch spellings
    # before falling back to the first path segment as a best effort.
    for width in range(min(4, len(parts)), 0, -1):
        candidate = "/".join(parts[:width])
        if candidate in {"main", "master", "develop", "dev", "trunk"} or re.match(r"^(v?\d+(?:\.\d+){1,3}|release/.+|feat/.+|feature/.+)$", candidate):
            return candidate, "/".join(parts[width:])
    return parts[0], "/".join(parts[1:])


def _safe_github_part(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.-]{1,100}$", value or ""))


def _fetch_github_repository(info: dict[str, str], max_chars: int) -> dict[str, Any]:
    repo_url = f"https://github.com/{info['owner']}/{info['repo']}.git"
    target = _github_cache_dir(info["owner"], info["repo"])
    attempts: list[dict[str, Any]] = []
    clone_result = _ensure_github_clone(repo_url, target, info.get("ref") or "")
    attempts.append({
        "provider": "github_clone",
        "ok": clone_result["ok"],
        "status": clone_result["status"],
        "url": repo_url,
        "chars": 0,
        "error": clone_result.get("error", ""),
    })
    if not clone_result["ok"]:
        return {
            "ok": False,
            "source_type": f"github_{info['type']}",
            "url": info.get("url") or repo_url,
            "fetched_url": repo_url,
            "provider": "github_clone",
            "fallback_used": False,
            "attempts": attempts,
            "status": "error",
            "final_url": info.get("url") or repo_url,
            "title": f"{info['owner']}/{info['repo']}",
            "content_type": "text/plain",
            "text": "",
            "chars": 0,
            "truncated": False,
            "error": clone_result.get("error") or "git clone failed",
        }

    if info["type"] == "commit" and info.get("ref"):
        text = _github_commit_summary(target, info["ref"], max_chars=max_chars)
    else:
        text = _github_tree_summary(target, info.get("path") or "", max_chars=max_chars)
    return {
        "ok": True,
        "source_type": f"github_{info['type']}",
        "url": info.get("url") or repo_url,
        "fetched_url": str(target),
        "provider": "github_clone",
        "fallback_used": False,
        "attempts": attempts,
        "status": "ok",
        "final_url": info.get("url") or repo_url,
        "title": f"{info['owner']}/{info['repo']}",
        "content_type": "text/markdown",
        "text": text,
        "chars": len(text),
        "truncated": len(text) > max_chars,
        "local_path": str(target),
    }


def _github_cache_dir(owner: str, repo: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{owner}__{repo}")
    return str(metis_dir("research", "github", slug))


def _ensure_github_clone(repo_url: str, target: str, ref: str = "") -> dict[str, Any]:
    if os.path.isdir(os.path.join(target, ".git")):
        fetch = _run_git(["git", "-C", target, "fetch", "--depth", "1", "origin"])
        if ref:
            _run_git(["git", "-C", target, "fetch", "--depth", "1", "origin", ref])
            _run_git(["git", "-C", target, "checkout", "--detach", "FETCH_HEAD"])
        else:
            _run_git(["git", "-C", target, "checkout", "-q", "HEAD"])
        return {"ok": True, "status": "updated" if fetch["ok"] else "cached", "error": fetch.get("error", "")}
    os.makedirs(os.path.dirname(target), exist_ok=True)
    command = ["git", "clone", "--depth", "1", "--filter=blob:none", repo_url, target]
    if ref:
        command = ["git", "clone", "--depth", "1", "--filter=blob:none", "--branch", ref, repo_url, target]
    clone = _run_git(command, timeout=90)
    if not clone["ok"] and ref:
        clone = _run_git(["git", "clone", "--depth", "1", "--filter=blob:none", repo_url, target], timeout=90)
        if clone["ok"]:
            _run_git(["git", "-C", target, "fetch", "--depth", "1", "origin", ref])
            _run_git(["git", "-C", target, "checkout", "--detach", "FETCH_HEAD"])
    return {"ok": clone["ok"], "status": "cloned" if clone["ok"] else "error", "error": clone.get("error", "")}


def _run_git(command: list[str], timeout: int = 35) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    if completed.returncode != 0:
        return {"ok": False, "error": (completed.stderr or completed.stdout or "git failed")[:500]}
    return {"ok": True, "stdout": completed.stdout[:500]}


def _github_tree_summary(root: str, subpath: str, max_chars: int) -> str:
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, subpath)) if subpath else base
    if not target.startswith(base) or not os.path.exists(target):
        target = base
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [name for name in dirnames if name not in {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}][:30]
        for filename in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, filename), base).replace("\\", "/")
            files.append(rel)
            if len(files) >= 160:
                break
        if len(files) >= 160:
            break
    selected = _select_summary_files(base, files)
    lines = [f"# GitHub repository snapshot", "", f"Local cache: {base}", "", "## Files", ""]
    lines.extend(f"- {path}" for path in files[:120])
    if len(files) > 120:
        lines.append(f"- ... {len(files) - 120} more")
    for rel in selected:
        content = _read_repo_text_file(base, rel, max_chars=max(1200, max_chars // max(1, len(selected))))
        if content:
            lines.extend(["", f"## {rel}", "", content.strip()])
    text = "\n".join(lines).strip()
    return text[:max_chars]


def _github_commit_summary(root: str, commit: str, max_chars: int) -> str:
    show = _run_git(["git", "-C", root, "show", "--stat", "--oneline", "--decorate", "--no-renames", commit], timeout=45)
    if not show["ok"]:
        show = _run_git(["git", "-C", root, "show", "--stat", "--oneline", "--decorate", "--no-renames", "HEAD"], timeout=45)
    text = show.get("stdout") or show.get("error") or ""
    return text[:max_chars]


def _select_summary_files(root: str, files: list[str]) -> list[str]:
    preferred_names = {
        "readme.md",
        "readme.txt",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "cargo.toml",
        "go.mod",
        "pnpm-lock.yaml",
        "vite.config.ts",
        "next.config.js",
    }
    selected: list[str] = []
    for rel in files:
        name = os.path.basename(rel).lower()
        if name in preferred_names:
            selected.append(rel)
        if len(selected) >= 6:
            break
    if not selected:
        selected = [rel for rel in files if _repo_text_candidate(root, rel)][:4]
    return selected[:6]


def _repo_text_candidate(root: str, rel: str) -> bool:
    ext = os.path.splitext(rel)[1].lower()
    if ext not in {".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}:
        return False
    path = os.path.join(root, rel)
    try:
        return os.path.getsize(path) <= 256_000
    except OSError:
        return False


def _read_repo_text_file(root: str, rel: str, max_chars: int) -> str:
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, rel))
    if not path.startswith(base) or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)
    except OSError:
        return ""


def _jina_reader_url(url: str) -> str:
    target = clean_user_source_url(str(url or "").strip())
    return f"https://r.jina.ai/{quote(target, safe=':/?#[]@!$&()*+,;=%')}"


def _needs_reader_fallback(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return True
    if result.get("status") == "partial":
        return True
    return int(result.get("chars") or 0) < 300


def _is_better_read(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    if candidate.get("ok") and not current.get("ok"):
        return True
    if not candidate.get("ok"):
        return False
    current_chars = int(current.get("chars") or len(str(current.get("text") or "")))
    candidate_chars = int(candidate.get("chars") or len(str(candidate.get("text") or "")))
    return candidate_chars > max(300, current_chars * 2)


def _attempt_summary(provider: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": provider,
        "ok": bool(result.get("ok")),
        "status": result.get("status") or ("ok" if result.get("ok") else "error"),
        "url": clean_user_source_url(str(result.get("final_url") or result.get("url") or "")),
        "chars": result.get("chars") or len(str(result.get("text") or "")),
        "error": result.get("error") or "",
    }


def _format_attempts(value: Any) -> str:
    attempts = value if isinstance(value, list) else []
    if not attempts:
        return "(none)"
    return "; ".join(
        f"{item.get('provider')}={item.get('status')}{'/' + str(item.get('chars')) + ' chars' if item.get('ok') else ''}"
        for item in attempts
        if isinstance(item, dict)
    )


def _fetch_activity_payload(
    result: dict[str, Any],
    *,
    phases: list[dict[str, Any]] | None = None,
    source_status: str | None = None,
) -> dict[str, Any]:
    row_status = source_status or ("opened" if result.get("ok") else "failed")
    opened = 1 if row_status == "opened" else 0
    failures = 1 if row_status == "failed" else 0
    source_url = clean_user_source_url(str(result.get("url") or result.get("final_url") or ""))
    if not source_url:
        source_url = clean_user_source_url(str(result.get("final_url") or ""))
    title = _source_title(result.get("title"), source_url)
    return {
        "schema": "metis.research_activity.v1",
        "kind": "fetch_content",
        "title": title,
        "query": source_url,
        "providers": [item.get("provider") for item in result.get("attempts", []) if isinstance(item, dict)],
        "stats": {
            "search_results": 0,
            "sources": 1,
            "opened": opened,
            "failures": failures,
            "partial": 1 if result.get("status") == "partial" else 0,
        },
        "phases": phases or [
            {"id": "classify", "label": "识别内容类型", "status": "complete", "summary": result.get("source_type") or "web_page"},
            {"id": "fetch", "label": "读取内容", "status": "complete" if result.get("ok") else "error", "summary": result.get("provider") or ""},
            {"id": "fallback", "label": "备用读取", "status": "complete" if result.get("fallback_used") else "skipped"},
        ],
        "sources": [
            {
                "id": "s1",
                "rank": 1,
                "title": title,
                "url": source_url,
                "domain": _source_domain(source_url),
                "status": row_status,
                "evidence_status": result.get("status") or "",
                "chars": result.get("chars") or 0,
                "error": result.get("error") or "",
            }
        ],
        "attempts": result.get("attempts") or [],
    }


def _fetch_running_phases(source_type: str, *, fetch_status: str, fallback_status: str) -> list[dict[str, Any]]:
    return [
        {"id": "classify", "label": "识别内容类型", "status": "complete", "summary": source_type or "web_page"},
        {"id": "fetch", "label": "读取内容", "status": fetch_status},
        {"id": "fallback", "label": "备用读取", "status": fallback_status},
    ]


def _persist_fetch_activity_payload(payload: dict[str, Any], *, report: str = "", status: str = "running") -> None:
    try:
        job = save_research_activity_job(payload, report=report, status=status)
        payload["job_id"] = job.get("id") or ""
        payload["job_status"] = job.get("status") or ""
    except Exception as exc:
        payload["job_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"


def _persist_fetch_activity(result: dict[str, Any]) -> None:
    payload = result.get("research_activity") if isinstance(result.get("research_activity"), dict) else None
    if not payload:
        return
    try:
        job = save_research_activity_job(payload, report=str(result.get("text") or ""), status="complete" if result.get("ok") else "error")
        payload["job_id"] = job.get("id") or ""
        payload["job_status"] = job.get("status") or ""
    except Exception as exc:
        payload["job_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"


def _fetch_visible_final_url(target: str, normalized: str, primary: dict[str, Any], best: dict[str, Any]) -> str:
    if best is primary:
        return clean_user_source_url(str(best.get("final_url") or best.get("url") or normalized))
    return clean_user_source_url(str(primary.get("final_url") or primary.get("url") or target or normalized))


def _source_title(title: Any, url: str) -> str:
    value = str(title or "").strip()
    lowered = value.lower()
    if value and lowered not in {"(untitled)", "untitled", "未命名来源"} and "r.jina.ai" not in lowered:
        return value
    domain = _source_domain(url)
    return domain or clean_user_source_url(url) or "来源"


def _source_domain(url: str) -> str:
    parsed = urlparse(clean_user_source_url(str(url or "")))
    return re.sub(r"^www\.", "", parsed.netloc, flags=re.IGNORECASE)
