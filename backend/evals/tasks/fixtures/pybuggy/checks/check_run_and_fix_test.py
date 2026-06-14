import subprocess
import sys

proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_pipeline.py", "-q"], text=True)
raise SystemExit(proc.returncode)
