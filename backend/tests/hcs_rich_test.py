"""Phase 8 acceptance: rich rootfs in production path (via service, non-elevated).
Verifies the rich bundle boots + the office library is actually usable.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Use a workspace root in a temp dir so create+run agree on session location.
_ws = Path(tempfile.mkdtemp(prefix="metis_rich_test_"))
os.environ["MIRO_WORKSPACE_ROOT"] = str(_ws)
os.environ.pop("METIS_VM_BUNDLE_PATH", None)  # use installed bundle, not dev

print("=" * 60)
print("Phase 8 acceptance — rich rootfs, office library inside VM")
print("=" * 60)

from backend.runtime.runtime_job import metis_runtime_job

# Command that exercises:
#  1. python3.12 in the VM
#  2. openpyxl (the headline office library)
#  3. file I/O via the host workspace push/pull
cmd = (
    "echo === BOOT_OK ===; "
    "python3 --version; "
    "python3 -c 'import openpyxl, pandas, docx, pptx, reportlab, PIL, requests; "
    "print(\"libs_ok openpyxl=\"+openpyxl.__version__+\" pandas=\"+pandas.__version__)' ; "
    "python3 -c 'import openpyxl; "
    "wb=openpyxl.Workbook(); ws=wb.active; ws[\"A1\"]=\"Quarter\"; ws[\"B1\"]=\"Revenue\"; "
    "ws.append([\"Q1\",100]); ws.append([\"Q2\",150]); ws.append([\"Q3\",200]); ws.append([\"Q4\",250]); "
    "wb.save(\"report.xlsx\"); "
    "wb2=openpyxl.load_workbook(\"report.xlsx\"); ws2=wb2.active; "
    "total=sum(r[1].value for r in ws2.iter_rows(min_row=2)); "
    "print(\"xlsx_round_trip total=\"+str(total))'"
)

result = json.loads(metis_runtime_job(
    task="rich rootfs acceptance",
    command=cmd,
    root=str(_ws),
    backend="hcs",
    timeout=180,
    collect_artifacts=True,
))

print()
print("status:", result.get("status"))
print("backend:", result.get("backend"))
print("returncode:", result.get("returncode"))
print("fallback_reason:", (result.get("run") or {}).get("fallback_reason"))
print("---- stdout ----")
print(result.get("stdout") or "(empty)")
print("---- stderr ----")
err = (result.get("stderr") or "")[:800]
print(err if err else "(empty)")

passed = (
    result.get("backend") == "hcs"
    and result.get("returncode") == 0
    and "BOOT_OK" in (result.get("stdout") or "")
    and "libs_ok" in (result.get("stdout") or "")
    and "xlsx_round_trip total=700" in (result.get("stdout") or "")
)
print()
print("=" * 60)
print("PHASE 8 ACCEPTANCE PASSED" if passed else "PHASE 8 ACCEPTANCE FAILED")
print("=" * 60)

import shutil
shutil.rmtree(_ws, ignore_errors=True)
sys.exit(0 if passed else 1)
