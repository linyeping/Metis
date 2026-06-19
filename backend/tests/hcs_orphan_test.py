"""Phase 6 admin test: orphan VM reaping via force_terminate_vm_by_id + cleanup_orphan_vms."""
import os
import sys
os.environ["METIS_VM_BUNDLE_PATH"] = r"D:\pycharm\py.project\Miro\backend\runtime\vmpack_build"

from backend.runtime.hcs_client import (
    HcsVm, find_any_bundle, is_hcs_available,
    enumerate_compute_systems, force_terminate_vm_by_id,
)
from backend.runtime.hcs_runtime import cleanup_orphan_vms

print("=" * 56)
print("Phase 6 — orphan VM reaping")
print("=" * 56)

ok, reason = is_hcs_available()
print(f"hcs: {ok} ({reason})")
if not ok:
    sys.exit(0)
bundle = find_any_bundle()
if not bundle:
    print("no bundle"); sys.exit(0)

# Boot a VM and DON'T track it in any session -> it is an "orphan".
vm = HcsVm(bundle, memory_mb=512, processors=1)
vm.create(); vm.start()
vid = vm.vm_id
print(f"[1] booted orphan VM {vid}")

ids = [v.get("Id") for v in enumerate_compute_systems()]
print(f"[2] enumerated, present={vid in ids}")

# Reap via cleanup_orphan_vms (no in-process session tracks it).
res = cleanup_orphan_vms()
print(f"[3] cleanup_orphan_vms: reaped={res.get('reaped')} errors={res.get('errors')}")

ids_after = [v.get("Id") for v in enumerate_compute_systems()]
gone = vid not in ids_after
print(f"[4] VM gone after cleanup: {gone}")

# Local handle cleanup (no-op since already terminated).
try:
    vm.destroy()
except Exception as exc:
    print(f"   destroy after reap: {exc}")

print("\n" + ("ORPHAN REAP PASSED" if (vid in ids and gone and vid in res.get("reaped", [])) else "ORPHAN REAP FAILED"))
