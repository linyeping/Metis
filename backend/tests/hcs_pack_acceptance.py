"""Phase 4 acceptance: hcs_runtime works from the INSTALLED bundle, zero dev config.

Explicitly removes METIS_VM_BUNDLE_PATH so discovery must use the standard
%LOCALAPPDATA%\\Metis\\vm_bundles\\metisvm.bundle install location.

Run as admin.
"""
import os
import sys
import tempfile
from pathlib import Path

# Ensure NO dev override — must find the installed pack on its own.
os.environ.pop("METIS_VM_BUNDLE_PATH", None)

print("=" * 60)
print("Phase 4 Acceptance — installed runtime pack, zero dev config")
print("=" * 60)

from backend.runtime.hcs_client import find_metis_bundle
from backend.runtime.hcs_runtime import (
    hcs_runtime_available,
    hcs_runtime_create_session,
    hcs_runtime_run,
    hcs_runtime_destroy,
)

bundle = find_metis_bundle()
print(f"discovered bundle: {bundle}")
if not bundle:
    print("FAIL: no installed bundle found at standard path")
    sys.exit(1)

ok, reason = hcs_runtime_available()
print(f"hcs_runtime_available: {ok} ({reason})")
if not ok:
    print("SKIP")
    sys.exit(0)

ws = Path(tempfile.mkdtemp(prefix="metis_acc_ws_"))
art = Path(tempfile.mkdtemp(prefix="metis_acc_art_"))
diag = Path(tempfile.mkdtemp(prefix="metis_acc_diag_"))
(ws / "report.txt").write_text("quarterly numbers: 1 2 3")

sid = "phase4-acceptance"
cr = hcs_runtime_create_session(sid, ws, art, diag)
print(f"\ncreate_session: ok={cr.get('ok')} boot_ms={cr.get('boot_ms')} "
      f"exec_mode={cr.get('exec_mode')} bundle={cr.get('bundle')}")

passed = False
if cr.get("ok"):
    rr = hcs_runtime_run(
        sid,
        command="wc -w < report.txt > wordcount.txt; cat report.txt; echo; cat wordcount.txt",
        timeout=20,
    )
    print(f"run: ok={rr.get('ok')} rc={rr.get('returncode')} "
          f"pushed={rr.get('files_pushed')} pulled={rr.get('files_pulled')}")
    print(f"stdout={rr.get('stdout')!r}")

    pulled = art / "wordcount.txt"
    if pulled.is_file():
        print(f"pulled artifact wordcount.txt = {pulled.read_text().strip()!r}")
        passed = rr.get("ok") and rr.get("returncode") == 0

    hcs_runtime_destroy(sid)

import shutil
for d in (ws, art, diag):
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("ACCEPTANCE PASSED" if passed else "ACCEPTANCE FAILED")
print("=" * 60)
sys.exit(0 if passed else 1)
