"""
Metis HCS Proof-of-Concept
==========================
Minimal script to verify that Python ctypes can create and boot
a Hyper-V lightweight Linux VM via the HCS (Host Compute Service).

Uses Claude Code Desktop's existing VM bundle on this machine as
the test rootfs — we are NOT copying or redistributing any assets,
just proving the API call chain works.

Requirements:
  - Virtual Machine Platform enabled
  - Current user in Hyper-V Administrators group (or run as admin)
  - Claude Code Desktop VM bundle at the expected path

Usage (run as admin):
  python -m backend.runtime.hcs_poc
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# HCS DLL bindings
# ---------------------------------------------------------------------------

_hcs = ctypes.WinDLL("computecore")

# Type aliases
HCS_OPERATION = ctypes.c_void_p
HCS_SYSTEM = ctypes.c_void_p
HCS_PROCESS = ctypes.c_void_p
HRESULT = ctypes.HRESULT
PCWSTR = ctypes.c_wchar_p
INFINITE = 0xFFFFFFFF


def _setup_bindings():
    """Declare argtypes/restypes for every HCS function we use."""

    # --- Operation lifecycle ---
    _hcs.HcsCreateOperation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _hcs.HcsCreateOperation.restype = HCS_OPERATION

    _hcs.HcsWaitForOperationResult.argtypes = [
        HCS_OPERATION,
        wintypes.DWORD,
        ctypes.POINTER(PCWSTR),
    ]
    _hcs.HcsWaitForOperationResult.restype = HRESULT

    _hcs.HcsGetOperationResultAndProcessInfo.argtypes = [
        HCS_OPERATION,
        ctypes.POINTER(ctypes.c_void_p),  # HCS_PROCESS_INFORMATION*
        ctypes.POINTER(PCWSTR),
    ]
    _hcs.HcsGetOperationResultAndProcessInfo.restype = HRESULT

    _hcs.HcsCloseOperation.argtypes = [HCS_OPERATION]
    _hcs.HcsCloseOperation.restype = None

    # --- Compute system lifecycle ---
    _hcs.HcsCreateComputeSystem.argtypes = [
        PCWSTR,                             # id
        PCWSTR,                             # configuration (JSON)
        HCS_OPERATION,                      # operation
        ctypes.c_void_p,                    # security descriptor (NULL)
        ctypes.POINTER(HCS_SYSTEM),         # OUT system handle
    ]
    _hcs.HcsCreateComputeSystem.restype = HRESULT

    _hcs.HcsStartComputeSystem.argtypes = [
        HCS_SYSTEM,
        HCS_OPERATION,
        PCWSTR,
    ]
    _hcs.HcsStartComputeSystem.restype = HRESULT

    _hcs.HcsShutDownComputeSystem.argtypes = [
        HCS_SYSTEM,
        HCS_OPERATION,
        PCWSTR,
    ]
    _hcs.HcsShutDownComputeSystem.restype = HRESULT

    _hcs.HcsTerminateComputeSystem.argtypes = [
        HCS_SYSTEM,
        HCS_OPERATION,
        PCWSTR,
    ]
    _hcs.HcsTerminateComputeSystem.restype = HRESULT

    _hcs.HcsCloseComputeSystem.argtypes = [HCS_SYSTEM]
    _hcs.HcsCloseComputeSystem.restype = None

    _hcs.HcsGetComputeSystemProperties.argtypes = [
        HCS_SYSTEM,
        HCS_OPERATION,
        PCWSTR,
    ]
    _hcs.HcsGetComputeSystemProperties.restype = HRESULT

    # --- Process in guest ---
    _hcs.HcsCreateProcess.argtypes = [
        HCS_SYSTEM,                         # system
        PCWSTR,                             # processParameters (JSON)
        HCS_OPERATION,                      # operation
        ctypes.c_void_p,                    # security descriptor (NULL)
        ctypes.POINTER(HCS_PROCESS),        # OUT process handle
    ]
    _hcs.HcsCreateProcess.restype = HRESULT

    # --- Enumeration ---
    _hcs.HcsEnumerateComputeSystems.argtypes = [PCWSTR, HCS_OPERATION]
    _hcs.HcsEnumerateComputeSystems.restype = HRESULT


_setup_bindings()


# ---------------------------------------------------------------------------
# HCS helpers
# ---------------------------------------------------------------------------

class HcsError(Exception):
    def __init__(self, function: str, hr: int, detail: str = ""):
        self.hr = hr & 0xFFFFFFFF
        self.function = function
        self.detail = detail
        super().__init__(f"{function} failed: HRESULT=0x{self.hr:08X} {detail}")


def _new_op() -> HCS_OPERATION:
    op = _hcs.HcsCreateOperation(None, None)
    if not op:
        raise HcsError("HcsCreateOperation", 0, "returned NULL")
    return op


def _wait_op(op: HCS_OPERATION, timeout_ms: int = INFINITE) -> Optional[str]:
    result_doc = PCWSTR()
    hr = _hcs.HcsWaitForOperationResult(op, timeout_ms, ctypes.byref(result_doc))
    text = result_doc.value if result_doc.value else None
    if hr != 0:
        raise HcsError("HcsWaitForOperationResult", hr, text or "")
    return text


_OP_PLACEHOLDER = object()


def _op_call(fn_name: str, args_with_op: list, timeout_ms: int = 30000) -> Optional[str]:
    """Call an HCS function with an operation handle at the specified position, then wait."""
    op = _new_op()
    resolved = [op if a is _OP_PLACEHOLDER else a for a in args_with_op]
    try:
        fn = getattr(_hcs, fn_name)
        hr = fn(*resolved)
        if hr != 0:
            raise HcsError(fn_name, hr)
        return _wait_op(op, timeout_ms)
    finally:
        _hcs.HcsCloseOperation(op)


# ---------------------------------------------------------------------------
# VM compute document
# ---------------------------------------------------------------------------

def build_compute_document(
    bundle: Path,
    *,
    memory_mb: int = 1024,
    processor_count: int = 2,
) -> Dict[str, Any]:
    """Build a minimal HCS V2 compute document for a Linux VM."""
    vmlinuz = bundle / "vmlinuz"
    initrd = bundle / "initrd"
    rootfs = bundle / "rootfs.vhdx"

    for f in (vmlinuz, initrd, rootfs):
        if not f.is_file():
            raise FileNotFoundError(f"VM asset missing: {f}")

    doc: Dict[str, Any] = {
        "Owner": "MetisPoC",
        "SchemaVersion": {"Major": 2, "Minor": 1},
        "ShouldTerminateOnLastHandleClosed": True,
        "VirtualMachine": {
            "StopOnReset": True,
            "Chipset": {
                "LinuxKernelDirect": {
                    "KernelFilePath": str(vmlinuz.resolve()),
                    "InitRdPath": str(initrd.resolve()),
                    "KernelCmdLine": "console=ttyS0 quiet",
                },
            },
            "ComputeTopology": {
                "Memory": {
                    "SizeInMB": memory_mb,
                    "AllowOvercommit": True,
                },
                "Processor": {
                    "Count": processor_count,
                },
            },
            "Devices": {
                "Scsi": {
                    "primary": {
                        "Attachments": {
                            "0": {
                                "Type": "VirtualDisk",
                                "Path": str(rootfs.resolve()),
                                "ReadOnly": True,
                            },
                        },
                    },
                },
            },
        },
    }

    sessiondata = bundle / "sessiondata.vhdx"
    if sessiondata.is_file():
        doc["VirtualMachine"]["Devices"]["Scsi"]["primary"]["Attachments"]["1"] = {
            "Type": "VirtualDisk",
            "Path": str(sessiondata.resolve()),
            "ReadOnly": False,
        }

    smol_bin = bundle / "smol-bin.vhdx"
    if smol_bin.is_file():
        doc["VirtualMachine"]["Devices"]["Scsi"]["primary"]["Attachments"]["2"] = {
            "Type": "VirtualDisk",
            "Path": str(smol_bin.resolve()),
            "ReadOnly": True,
        }

    return doc


# ---------------------------------------------------------------------------
# PoC main flow
# ---------------------------------------------------------------------------

def find_claude_bundle() -> Optional[Path]:
    """Locate Claude Code Desktop's VM bundle on this machine."""
    candidates = [
        Path(r"E:\ClaudeCode\cache\vm_bundles\claudevm.bundle"),
        Path.home() / "AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/vm_bundles/claudevm.bundle",
    ]
    for p in candidates:
        if (p / "vmlinuz").is_file() and (p / "rootfs.vhdx").is_file():
            return p
    return None


def enumerate_vms() -> list:
    """List currently running HCS compute systems."""
    result = _op_call("HcsEnumerateComputeSystems", [PCWSTR("{}"), _OP_PLACEHOLDER], timeout_ms=5000)
    if result:
        return json.loads(result)
    return []


def create_and_boot_vm(bundle: Path, vm_id: str = "") -> Tuple[HCS_SYSTEM, str]:
    """Create and start a lightweight Linux VM. Returns (system_handle, vm_id)."""
    if not vm_id:
        vm_id = f"metis-poc-{uuid.uuid4().hex[:8]}"

    doc = build_compute_document(bundle, memory_mb=1024, processor_count=2)
    config_json = json.dumps(doc, ensure_ascii=False)

    print(f"[HCS] Creating compute system: {vm_id}")

    # Create
    system = HCS_SYSTEM()
    op = _new_op()
    try:
        hr = _hcs.HcsCreateComputeSystem(
            PCWSTR(vm_id),
            PCWSTR(config_json),
            op,
            None,
            ctypes.byref(system),
        )
        if hr != 0:
            raise HcsError("HcsCreateComputeSystem", hr)
        result = _wait_op(op, 30000)
        print(f"[HCS] Created: {result or 'ok'}")
    finally:
        _hcs.HcsCloseOperation(op)

    # Start
    print(f"[HCS] Starting compute system...")
    _op_call("HcsStartComputeSystem", [system, _OP_PLACEHOLDER, None], timeout_ms=60000)
    print(f"[HCS] VM started: {vm_id}")

    return system, vm_id


def get_vm_properties(system: HCS_SYSTEM) -> Dict[str, Any]:
    """Query properties of a running VM."""
    query = json.dumps({
        "PropertyTypes": ["Statistics", "Memory"],
    })
    result = _op_call("HcsGetComputeSystemProperties", [system, _OP_PLACEHOLDER, PCWSTR(query)], timeout_ms=5000)
    if result:
        return json.loads(result)
    return {}


def terminate_vm(system: HCS_SYSTEM):
    """Force-terminate a VM."""
    print("[HCS] Terminating compute system...")
    try:
        _op_call("HcsTerminateComputeSystem", [system, _OP_PLACEHOLDER, None], timeout_ms=10000)
        print("[HCS] Terminated.")
    except HcsError as e:
        if e.hr == 0xC0370105:  # HCS_E_INVALID_STATE (already stopped)
            print("[HCS] Already stopped.")
        else:
            raise
    finally:
        _hcs.HcsCloseComputeSystem(system)


def run_poc():
    """Full PoC: find bundle → create VM → boot → query properties → terminate."""
    print("=" * 60)
    print("Metis HCS Proof-of-Concept")
    print("=" * 60)

    # Step 1: Find Claude's VM bundle
    bundle = find_claude_bundle()
    if not bundle:
        print("ERROR: Claude Code Desktop VM bundle not found.")
        print("Expected: E:\\ClaudeCode\\cache\\vm_bundles\\claudevm.bundle\\")
        sys.exit(1)

    print(f"\n[1/5] VM bundle found: {bundle}")
    for f in ("vmlinuz", "initrd", "rootfs.vhdx", "sessiondata.vhdx", "smol-bin.vhdx"):
        p = bundle / f
        if p.is_file():
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"  {f}: {size_mb:.1f} MB")
        else:
            print(f"  {f}: MISSING")

    # Step 2: List existing VMs
    print(f"\n[2/5] Enumerating existing compute systems...")
    try:
        vms = enumerate_vms()
        print(f"  Found {len(vms)} running compute system(s)")
        for vm in vms:
            print(f"  - {vm.get('Id', '?')[:40]}  Owner={vm.get('Owner', '?')}")
    except HcsError as e:
        print(f"  Enumerate failed (may need admin): {e}")

    # Step 3: Create and boot VM
    print(f"\n[3/5] Creating and booting Linux VM...")
    t0 = time.time()
    system = None
    vm_id = ""
    try:
        system, vm_id = create_and_boot_vm(bundle)
        boot_ms = int((time.time() - t0) * 1000)
        print(f"  Boot completed in {boot_ms}ms")
    except HcsError as e:
        print(f"  FAILED: {e}")
        print(f"\n  If HRESULT=0x8037011B: run this script as Administrator")
        print(f"  If HRESULT=0x80370102: enable Virtual Machine Platform in Windows Features")
        sys.exit(1)

    try:
        # Step 4: Query VM properties
        print(f"\n[4/5] Querying VM properties...")
        try:
            props = get_vm_properties(system)
            print(f"  Properties: {json.dumps(props, indent=2, ensure_ascii=False)[:1000]}")
        except HcsError as e:
            print(f"  Properties query failed: {e}")
            print("  (This is non-fatal — VM is still running)")

        # Step 5: Wait a moment then terminate
        print(f"\n[5/5] VM is running. Waiting 3 seconds then terminating...")
        time.sleep(3)

    finally:
        if system:
            terminate_vm(system)

    print(f"\n{'=' * 60}")
    print("PoC PASSED — HCS VM lifecycle works from Python ctypes!")
    print(f"{'=' * 60}")
    print(f"\nNext steps:")
    print(f"  1. Add HcsCreateProcess to run commands inside the VM")
    print(f"  2. Set up vsock communication for guest agent protocol")
    print(f"  3. Build Metis-owned rootfs (not Claude's)")


if __name__ == "__main__":
    run_poc()
