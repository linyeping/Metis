import subprocess
import sys
from pathlib import Path

script = Path("scripts/summarize_orders.py")
assert script.is_file(), "scripts/summarize_orders.py is required"
proc = subprocess.run([sys.executable, str(script), "data/orders.csv"], capture_output=True, text=True)
assert proc.returncode == 0, proc.stderr
out = proc.stdout.strip().replace("\r\n", "\n")
assert "books,17" in out
assert "tools,15" in out
