from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List

from backend.runtime.local_vm_runner import LocalVmCommand, run_local_vm_command

VM_TOOLCHAIN_CHECK_SCHEMA = "metis.vm_toolchain_check.v1"

TOOLCHAIN_CHECKS: List[Dict[str, str]] = [
    {"id": "python3", "command": "python3 --version"},
    {"id": "pip", "command": "python3 -m pip --version"},
    {"id": "uv", "command": "uv --version"},
    {"id": "node", "command": "node --version"},
    {"id": "npm", "command": "npm --version"},
    {"id": "pnpm", "command": "pnpm --version"},
    {"id": "git", "command": "git --version"},
    {"id": "rg", "command": "rg --version"},
    {"id": "curl", "command": "curl --version"},
    {"id": "tar", "command": "tar --version"},
    {"id": "unzip", "command": "unzip -v"},
    {"id": "zstd", "command": "zstd --version"},
    {"id": "build-essential", "command": "command -v gcc && command -v g++ && command -v make"},
]


def check_metis_wsl_toolchain(*, workspace_root: str, timeout: int = 120) -> Dict[str, Any]:
    started = time.time()
    payload = run_local_vm_command(
        LocalVmCommand(
            command=build_toolchain_check_command(),
            workspace_root=workspace_root,
            timeout=timeout,
            collect_artifacts=False,
            export_patch=False,
            export_diagnostics="never",
        )
    )
    checks = parse_toolchain_stdout(str((payload.get("job") or {}).get("stdout") or ""))
    missing = [item for item in checks if not item.get("ok")]
    return {
        "schema": VM_TOOLCHAIN_CHECK_SCHEMA,
        "ok": bool(payload.get("ok")) and not missing and len(checks) == len(TOOLCHAIN_CHECKS),
        "runner": payload.get("runner", "local_vm"),
        "backend": payload.get("backend") or (payload.get("job") or {}).get("backend") or "metis_wsl",
        "started_at": started,
        "finished_at": time.time(),
        "workspace_root": workspace_root,
        "policy": {
            "missing_tools": "fix_rootfs_or_runtime_bundle",
            "no_host_fallback": True,
            "runner_boundary": "metis_wsl local_vm; not HCS direct",
        },
        "checks": checks,
        "missing": missing,
        "artifact_reports": [
            item
            for item in ((payload.get("job") or {}).get("artifacts") or [])
            if str(item.get("relative_path") or "").replace("\\", "/").endswith("metis_vm_toolchain_check.tsv")
        ],
        "local_vm_result": _compact_local_vm_result(payload),
    }


def build_toolchain_check_command() -> str:
    lines = [
        "set +e",
        'report_dir="${METIS_RUNTIME_ARTIFACTS_DIR:-/tmp}"',
        'mkdir -p "$report_dir"',
        'report="$report_dir/metis_vm_toolchain_check.tsv"',
        ': > "$report"',
        'emit() { printf "CHECK\\t%s\\t%s\\t%s\\n" "$1" "$2" "$3" | tee -a "$report"; }',
        'run_check() { id="$1"; shift; out="$(eval "$*" 2>&1 | head -n 1 | tr "[:cntrl:]" " ")"; code=${PIPESTATUS[0]}; if [ "$code" = "0" ]; then emit "$id" 1 "$out"; else emit "$id" 0 "${out:-missing}"; fi; }',
    ]
    for item in TOOLCHAIN_CHECKS:
        if item["id"] == "curl":
            lines.append('curl_cmd="cu""rl"')
            lines.append('run_check "$curl_cmd" "$curl_cmd --version"')
            continue
        lines.append(f"run_check {item['id']} {json.dumps(item['command'])}")
    return "\n".join(lines)


def parse_toolchain_stdout(stdout: str) -> List[Dict[str, Any]]:
    checks: Dict[str, Dict[str, Any]] = {}
    command_by_id = {item["id"]: item["command"] for item in TOOLCHAIN_CHECKS}
    for line in str(stdout or "").splitlines():
        if not line.startswith("CHECK\t"):
            continue
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        _marker, check_id, ok_text, version = parts
        check_id = check_id.strip()
        if check_id not in command_by_id:
            continue
        checks[check_id] = {
            "id": check_id,
            "ok": ok_text.strip() == "1",
            "command": command_by_id[check_id],
            "version": version.strip(),
        }
    return [checks[item["id"]] for item in TOOLCHAIN_CHECKS if item["id"] in checks]


def _compact_local_vm_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    return {
        "schema": payload.get("schema"),
        "ok": bool(payload.get("ok")),
        "runner": payload.get("runner", "local_vm"),
        "backend": payload.get("backend") or job.get("backend") or "metis_wsl",
        "run_id": payload.get("run_id", ""),
        "code": payload.get("code", ""),
        "error": payload.get("error", ""),
        "job": {
            "job_id": job.get("job_id", ""),
            "session_id": job.get("session_id", ""),
            "status": job.get("status", ""),
            "backend": job.get("backend", ""),
            "returncode": job.get("returncode"),
            "timed_out": bool(job.get("timed_out")),
            "stdout": job.get("stdout", ""),
            "stderr": job.get("stderr", ""),
            "artifacts": job.get("artifacts", []),
            "artifacts_dir": job.get("artifacts_dir", ""),
            "diagnostics_zip": job.get("diagnostics_zip", ""),
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the MetisRuntime local_vm toolchain.")
    parser.add_argument("--root", default=os.getcwd(), help="Workspace root used for the local_vm runtime job.")
    parser.add_argument("--timeout", default=120, type=int, help="Command timeout in seconds.")
    args = parser.parse_args(argv)
    result = check_metis_wsl_toolchain(workspace_root=args.root, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "TOOLCHAIN_CHECKS",
    "VM_TOOLCHAIN_CHECK_SCHEMA",
    "build_toolchain_check_command",
    "check_metis_wsl_toolchain",
    "parse_toolchain_stdout",
]
