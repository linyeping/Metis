from pathlib import Path

answer = Path("answer.md")
assert answer.is_file(), "answer.md is required"
text = answer.read_text(encoding="utf-8")
assert "unused_alpha" in text
assert "unused_beta" in text
