"""Phase 8 net acceptance: VM internet access + pip install via rich rootfs."""
import json, os, sys, tempfile
from pathlib import Path

_ws = Path(tempfile.mkdtemp(prefix="metis_net_test_"))
os.environ["MIRO_WORKSPACE_ROOT"] = str(_ws)
os.environ.pop("METIS_VM_BUNDLE_PATH", None)

from backend.runtime.runtime_job import metis_runtime_job

cmd = (
    "echo === net check ===; "
    "ip addr show eth0 | grep 'inet '; "
    "echo --- curl test ---; "
    "curl -s --max-time 15 https://api.ipify.org && echo; "
    "echo --- pip install ---; "
    "pip install --quiet --no-cache-dir requests 2>&1 | tail -2; "
    "python3 -c 'import requests; r=requests.get(\"https://api.github.com\", timeout=10); print(\"github_status=\"+str(r.status_code))'"
)

result = json.loads(metis_runtime_job(
    task="net+pip",
    command=cmd,
    root=str(_ws),
    backend="hcs",
    timeout=120,
    allow_network=True,   # 关键:启用 HCN 网卡
))

print("backend:", result.get("backend"), "| rc:", result.get("returncode"))
print("---- stdout ----"); print(result.get("stdout") or "(empty)")
print("---- stderr ----"); print((result.get("stderr") or "")[:800] or "(empty)")

import shutil; shutil.rmtree(_ws, ignore_errors=True)