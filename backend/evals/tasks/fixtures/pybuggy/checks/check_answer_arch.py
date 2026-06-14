from pathlib import Path

answer = Path("answer.md")
assert answer.is_file(), "answer.md is required"
text = answer.read_text(encoding="utf-8").lower()
for keyword in ("pybuggy/app.py", "load_config", "normalize_records"):
    assert keyword in text, f"missing {keyword}"
