from pathlib import Path

answer = Path("answer.md")
assert answer.is_file(), "answer.md is required"
assert len(answer.read_text(encoding="utf-8").strip()) >= 20, "answer.md must contain a non-empty probe answer"
