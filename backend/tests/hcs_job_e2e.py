"""Definitive end-to-end: run a real metis_runtime_job on the HCS backend.

Exercises the production path: runtime_job -> isolated_runtime ->
_run_hcs_command -> hcs_runtime -> VM -> metisd. Run elevated.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Use the installed bundle explicitly (avoid LOCALAPPDATA virtualization ambiguity).
os.environ["METIS_VM_BUNDLE_PATH"] = r"C:\Users\20118\AppData\Local\Metis\vm_bundles\metisvm.bundle"

# Use a small temp dir AS the workspace root so create + run agree on session
# location and the snapshot stays tiny (mirrors the agent's root="." usage).
_ws = Path(tempfile.mkdtemp(prefix="metis_ws_"))
os.environ["MIRO_WORKSPACE_ROOT"] = str(_ws)

from backend.runtime.runtime_job import metis_runtime_job

# Job root == workspace root (the tiny temp dir).
root = _ws
(root / "seed.txt").write_text("seed-data-42")

print("=" * 56)
print("HCS Runtime Job E2E (production path, elevated)")
print("=" * 56)

result = metis_runtime_job(
    task="sandbox end-to-end proof",
    command="echo HELLO_FROM_SANDBOX; cat seed.txt; python3 -c \"print('sum=', sum(range(11)))\" > result.txt; cat result.txt",
    root=str(root),
    backend="hcs",
    timeout=60,
    collect_artifacts=True,
)

data = json.loads(result)
print("--- created ---"); print(json.dumps(data.get("created", {}), indent=2, ensure_ascii=True)[:1800])
print("--- run ---"); print(json.dumps(data.get("run", {}), indent=2, ensure_ascii=True)[:800])
print("--- top error ---", ascii(data.get("error")))
print("ok:", data.get("ok"))
print("status:", data.get("status"))
print("backend:", data.get("backend"))
print("returncode:", data.get("returncode"))
print("stdout:", repr(data.get("stdout")))
v = data.get("verifier", {})
print("verifier.ok:", v.get("ok"), "checks:", [(c.get("id"), c.get("ok")) for c in v.get("checks", [])])

import shutil
shutil.rmtree(root, ignore_errors=True)

passed = data.get("backend") == "hcs" and data.get("returncode") == 0 and "HELLO_FROM_SANDBOX" in str(data.get("stdout"))
print("\n" + ("JOB E2E PASSED (ran in HCS sandbox)" if passed else "JOB E2E FAILED"))
sys.exit(0 if passed else 1)
