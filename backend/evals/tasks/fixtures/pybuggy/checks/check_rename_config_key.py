from pathlib import Path

from pybuggy.config import DEFAULT_CONFIG
from pybuggy.service import retry_budget
from pybuggy.settings_view import public_settings

root = Path.cwd()
text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "pybuggy").glob("*.py"))
assert "retry_count" not in text
assert DEFAULT_CONFIG["max_retries"] == 3
assert retry_budget() == 3
assert public_settings()["max_retries"] == 3
