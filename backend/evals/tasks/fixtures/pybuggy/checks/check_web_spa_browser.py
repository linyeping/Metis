from pathlib import Path


text = Path("answer.md").read_text(encoding="utf-8").lower()

assert "todo" in text
assert any(token in text for token in ("react", "what needs to be done", "todomvc", "placeholder"))
