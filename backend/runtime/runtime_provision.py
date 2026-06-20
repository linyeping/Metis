"""
Metis runtime first-run provisioning.

Detects whether the HCS VM sandbox can run on this machine and, if not,
provisions the missing pieces with a single UAC elevation:

  - Virtual Machine Platform optional feature (enables computecore.dll / HCS)
  - membership in the "Hyper-V Administrators" group (so HCS works without
    per-launch admin elevation)

Enabling the feature requires one reboot.  Group membership takes effect
after the next sign-in (a reboot covers that too).  This matches the
accepted UX: one UAC prompt + one reboot.

Detection is read-only and safe to call anytime.  Provisioning writes a
PowerShell script and launches it elevated via ShellExecute("runas").
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from backend.runtime.hcs_client import find_metis_bundle, is_hcs_available

log = logging.getLogger("metis.provision")

PROVISION_SCHEMA = "metis.runtime_provision.status.v1"

# Well-known SID for the "Hyper-V Administrators" group (locale-independent).
HYPERV_ADMINS_SID = "S-1-5-32-578"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _classify_hcs(reason: str) -> Dict[str, bool]:
    """Infer VM-Platform / permission state from is_hcs_available's reason."""
    low = reason.lower()
    dll_missing = ("computecore" in low and "not found" in low) or "hyperv_not_installed" in low
    access_denied = "access_denied" in low or "0x8037011b" in low or "access denied" in low
    return {
        "vm_platform_enabled": not dll_missing,
        "permission_denied": access_denied,
    }


def _in_hyperv_admins_configured() -> bool:
    """Whether the current user is a configured member of Hyper-V Administrators
    (may require sign-out/reboot to become active in the token)."""
    try:
        ps = (
            "$ErrorActionPreference='Stop';"
            f"$g=Get-LocalGroup -SID '{HYPERV_ADMINS_SID}';"
            "$me=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;"
            "$m=Get-LocalGroupMember -Group $g | ForEach-Object { $_.Name };"
            "if ($m -contains $me) { 'YES' } else { 'NO' }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        return "YES" in (out.stdout or "")
    except Exception:
        return False


def _virtualization_ok() -> bool | None:
    """Whether hardware virtualization is usable (VT-x/AMD-V).

    Returns True/False, or None if it could not be determined. A running
    hypervisor (HypervisorPresent) implies virtualization is on; otherwise
    we read the firmware flag.  This catches the common 'VT-x disabled in
    BIOS' failure before it surfaces as a cryptic HCS boot error.
    """
    try:
        ps = (
            "$cs = Get-CimInstance Win32_ComputerSystem;"
            "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1;"
            "if ($cs.HypervisorPresent) { 'OK' }"
            "elseif ($cpu.VirtualizationFirmwareEnabled) { 'OK' }"
            "else { 'OFF' }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        text = (out.stdout or "").strip()
        if "OK" in text:
            return True
        if "OFF" in text:
            return False
        return None
    except Exception:
        return None


def _reboot_pending() -> bool:
    try:
        ps = (
            "$p=$false;"
            "if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending') {$p=$true};"
            "if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') {$p=$true};"
            "if ($p) {'YES'} else {'NO'}"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        return "YES" in (out.stdout or "")
    except Exception:
        return False


SERVICE_NAME = "MetisVMService"


def _service_state() -> Dict[str, bool]:
    """Whether the privileged service is installed / running / responding."""
    installed, running, responding = False, False, False
    try:
        out = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            installed = True
            running = "RUNNING" in (out.stdout or "")
    except Exception:
        pass
    try:
        from backend.runtime import svc_client
        responding = svc_client.service_available()
    except Exception:
        pass
    return {"installed": installed, "running": running, "responding": responding}


def _svc_exe_path() -> str:
    """Locate metis-vm-svc.exe (env override, app resources, or dev build)."""
    v = os.environ.get("METIS_VM_SVC_PATH", "")
    if v and os.path.isfile(v):
        return v
    candidates = []
    # production (frozen): <install>/resources/backend-dist/metis-backend/metis-backend.exe
    # -> <install>/resources/runtime-svc/metis-vm-svc.exe
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(str(exe_dir.parent.parent / "runtime-svc" / "metis-vm-svc.exe"))
    candidates += [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Metis", "resources", "runtime-svc", "metis-vm-svc.exe"),
        # dev build output
        str(Path(__file__).resolve().parent / "metis-vm-svc" / "metis-vm-svc.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def provision_status(deep: bool = False) -> Dict[str, Any]:
    """Detect HCS sandbox readiness and what provisioning steps remain.

    `deep=True` also queries group membership / reboot-pending (slower,
    spawns PowerShell). Default is the fast path based on HCS probing.
    """
    if sys.platform != "win32":
        return {
            "schema": PROVISION_SCHEMA, "ready": False, "supported": False,
            "reason": "HCS sandbox is Windows-only", "needs": [], "actions": [],
        }

    available, reason = is_hcs_available()
    cls = _classify_hcs(reason)
    bundle = find_metis_bundle()
    is_admin = _is_admin()

    needs: List[str] = []
    actions: List[Dict[str, str]] = []

    # Hardware virtualization (BIOS) — Metis cannot fix this for the user.
    virt_ok = _virtualization_ok() if deep else None
    if virt_ok is False:
        needs.append("enable_virtualization_bios")
        actions.append({
            "id": "enable_virtualization_bios",
            "title": "Enable CPU virtualization (VT-x/AMD-V) in BIOS/UEFI",
            "elevation": "manual",
            "reboot": "manual (BIOS)",
        })

    # Service model: a LocalSystem service does all HCS work, so the user does
    # NOT need Hyper-V Administrators membership — only VM Platform + the service.
    svc = _service_state()

    if not cls["vm_platform_enabled"]:
        needs.append("enable_vm_platform")
        actions.append({
            "id": "enable_vm_platform",
            "title": "Enable Virtual Machine Platform",
            "elevation": "admin",
            "reboot": "required",
        })

    if bundle is None:
        needs.append("install_pack")
        actions.append({
            "id": "install_pack",
            "title": "Install the Metis VM runtime pack",
            "elevation": "none",
            "reboot": "none",
        })

    if not svc["installed"]:
        needs.append("install_service")
        actions.append({
            "id": "install_service",
            "title": "Install the Metis sandbox service (runs VMs without per-task UAC)",
            "elevation": "admin",
            "reboot": "none",
        })
    elif not svc["responding"]:
        # Installed but the pipe isn't answering (stale/wedged instance) — a
        # restart re-creates the pipe + re-resolves the user ACL. Without this
        # the panel was stuck: not ready, but no actionable fix.
        needs.append("repair_service")
        actions.append({
            "id": "repair_service",
            "title": "Restart the Metis sandbox service (it is installed but not responding)",
            "elevation": "admin",
            "reboot": "none",
        })

    reboot_required = any(a.get("reboot") == "required" for a in actions)
    # Ready = the service can run jobs (responding) and the pack is installed.
    ready = svc["responding"] and bundle is not None

    return {
        "schema": PROVISION_SCHEMA,
        "supported": True,
        "ready": ready,
        "hcs_available": available,
        "hcs_reason": reason,
        "vm_platform_enabled": cls["vm_platform_enabled"],
        "permission_denied": cls["permission_denied"],
        "service_installed": svc["installed"],
        "service_running": svc["running"],
        "service_responding": svc["responding"],
        "svc_exe_path": _svc_exe_path(),
        "bundle_installed": bundle is not None,
        "bundle_path": str(bundle) if bundle else "",
        "is_admin": is_admin,
        "virtualization_ok": virt_ok,
        "reboot_pending": _reboot_pending() if deep else None,
        "needs": needs,
        "actions": actions,
        "reboot_required": reboot_required,
        "ux_summary": _ux_summary(ready, bundle is not None, needs, reboot_required),
    }


def _ux_summary(ready: bool, has_bundle: bool, needs: List[str], reboot_required: bool) -> str:
    if ready and has_bundle:
        return "Sandbox ready."
    if "enable_virtualization_bios" in needs:
        return ("CPU virtualization (VT-x/AMD-V) is disabled in your BIOS/UEFI. "
                "Enable it in firmware settings, then retry — Metis cannot change this for you.")
    parts = []
    if "enable_vm_platform" in needs:
        parts.append("enable Virtual Machine Platform")
    if "install_pack" in needs:
        parts.append("install the runtime pack")
    if "install_service" in needs:
        parts.append("install the sandbox service")
    tail = " (one UAC prompt"
    tail += " + one reboot)" if reboot_required else ")"
    return ("Setup needed: " + ", ".join(parts) + tail) if parts else "Setup needed."


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def build_provision_script(actions: List[str]) -> str:
    """Generate an idempotent elevated PowerShell provisioning script."""
    lines = [
        "$ErrorActionPreference = 'Continue'",
        "$results = @{}",
    ]
    if "enable_vm_platform" in actions:
        lines += [
            "Write-Output '[metis] enabling Virtual Machine Platform...'",
            "try {",
            "  $f = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform",
            "  if ($f.State -ne 'Enabled') {",
            "    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null",
            "    $results['vm_platform'] = 'enabled (reboot required)'",
            "  } else { $results['vm_platform'] = 'already enabled' }",
            "} catch { $results['vm_platform'] = 'error: ' + $_.Exception.Message }",
        ]
    if "install_service" in actions:
        svc_exe = _svc_exe_path()
        if svc_exe:
            lines += [
                "Write-Output '[metis] installing sandbox service...'",
                "try {",
                f"  & '{svc_exe}' install 2>&1 | Out-Null",
                "  $results['service'] = 'installed'",
                "} catch { $results['service'] = 'error: ' + $_.Exception.Message }",
            ]
        else:
            lines.append("$results['service'] = 'error: metis-vm-svc.exe not found'")
    if "repair_service" in actions:
        lines += [
            "Write-Output '[metis] restarting sandbox service...'",
            "try {",
            "  sc.exe stop MetisVMService | Out-Null",
            "  Start-Sleep -Seconds 1",
            "  sc.exe start MetisVMService | Out-Null",
            "  $results['service'] = 'restarted'",
            "} catch { $results['service'] = 'error: ' + $_.Exception.Message }",
        ]
    if "add_hyperv_admins" in actions:
        # Legacy path (non-service / direct elevated). Not used in the service model.
        lines += [
            "Write-Output '[metis] adding user to Hyper-V Administrators...'",
            "try {",
            f"  $grp = Get-LocalGroup -SID '{HYPERV_ADMINS_SID}'",
            "  $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
            "  $existing = Get-LocalGroupMember -Group $grp -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }",
            "  if ($existing -notcontains $me) {",
            "    Add-LocalGroupMember -Group $grp -Member $me",
            "    $results['hyperv_admins'] = 'added (sign-out/reboot to apply)'",
            "  } else { $results['hyperv_admins'] = 'already a member' }",
            "} catch { $results['hyperv_admins'] = 'error: ' + $_.Exception.Message }",
        ]
    lines += [
        "$results | ConvertTo-Json | Out-File -FilePath $env:METIS_PROVISION_RESULT -Encoding utf8",
        "Write-Output '[metis] provisioning done'",
    ]
    return "\r\n".join(lines) + "\r\n"


def run_provision_elevated(actions: List[str], timeout_s: int = 180) -> Dict[str, Any]:
    """Write the provisioning script and run it elevated (triggers one UAC).

    Returns the parsed result. The user must approve the UAC prompt.
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "Windows only"}
    actions = [a for a in actions if a in ("enable_vm_platform", "install_service", "repair_service", "add_hyperv_admins")]
    if not actions:
        return {"ok": True, "skipped": True, "message": "no elevated actions needed"}

    workdir = Path(tempfile.mkdtemp(prefix="metis_provision_"))
    script = workdir / "provision.ps1"
    result_file = workdir / "result.json"
    script.write_text(build_provision_script(actions), encoding="utf-8")

    # Launch elevated via ShellExecuteW("runas"). SW_SHOWNORMAL=1.
    params = (
        f'-NoProfile -ExecutionPolicy Bypass '
        f'-Command "$env:METIS_PROVISION_RESULT=\'{result_file}\'; '
        f'& \'{script}\'"'
    )
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 1)
    except Exception as exc:
        return {"ok": False, "error": f"ShellExecute failed: {exc}"}
    if int(rc) <= 32:
        # <=32 means failure; 5 == ERROR_ACCESS_DENIED (user declined UAC)
        return {"ok": False, "error": f"elevation failed or declined (code {int(rc)})", "shellexecute_code": int(rc)}

    # Wait for the elevated script to drop its result file.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if result_file.is_file():
            try:
                import json
                data = json.loads(result_file.read_text(encoding="utf-8-sig"))
                return {"ok": True, "actions": actions, "result": data}
            except Exception:
                time.sleep(0.5)
                continue
        time.sleep(0.5)
    return {"ok": True, "actions": actions, "result": None, "note": "provisioning launched; result not captured (still running or UAC pending)"}


__all__ = [
    "PROVISION_SCHEMA",
    "provision_status",
    "build_provision_script",
    "run_provision_elevated",
]
