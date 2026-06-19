"""Integrated hcs_runtime E2E: create_session -> run (push/pull) -> destroy.

Runs as admin. Uses the in-repo vmpack_build bundle via METIS_VM_BUNDLE_PATH.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["METIS_VM_BUNDLE_PATH"] = r"D:\pycharm\py.project\Miro\backend\runtime\vmpack_build"

print("=" * 60)
print("hcs_runtime Integrated E2E")
print("=" * 60)

from backend.runtime.hcs_runtime import (
    hcs_runtime_available,
    hcs_runtime_create_session,
    hcs_runtime_run,
    hcs_runtime_destroy,
)

ok, reason = hcs_runtime_available()
print(f"available: {ok} ({reason})")
if not ok:
    sys.exit(0)

ws = Path(tempfile.mkdtemp(prefix="metis_rt_ws_"))
art = Path(tempfile.mkdtemp(prefix="metis_rt_art_"))
diag = Path(tempfile.mkdtemp(prefix="metis_rt_diag_"))
(ws / "input.txt").write_text("data from host workspace")

sid = "rt-e2e-1"
print("\n[1] create_session...")
cr = hcs_runtime_create_session(sid, ws, art, diag, memory_mb=1024, processors=2)
print(f"    ok={cr.get('ok')} boot_ms={cr.get('boot_ms')} exec_mode={cr.get('exec_mode')}")

if cr.get("ok"):
    print("\n[2] run command (reads pushed input, writes output)...")
    rr = hcs_runtime_run(
        sid,
        command="cat input.txt; python3 -c \"open('output.txt','w').write('RESULT: ' + open('input.txt').read())\"",
        timeout=20,
    )
    print(f"    ok={rr.get('ok')} rc={rr.get('returncode')} dur={rr.get('duration_ms')}ms")
    print(f"    files_pushed={rr.get('files_pushed')} files_pulled={rr.get('files_pulled')}")
    print(f"    stdout={rr.get('stdout')!r}")
    if rr.get("stderr"):
        print(f"    stderr={rr.get('stderr')!r}")

    out = art / "output.txt"
    print(f"\n[3] artifact pulled to host? {out}")
    if out.is_file():
        print(f"    SUCCESS — {out.read_text()!r}")
    else:
        print(f"    output.txt not pulled")

    print("\n[4] destroy...")
    dr = hcs_runtime_destroy(sid)
    print(f"    ok={dr.get('ok')}")

import shutil
for d in (ws, art, diag):
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("DONE")
