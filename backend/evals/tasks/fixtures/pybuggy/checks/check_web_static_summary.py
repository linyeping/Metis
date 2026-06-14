from pathlib import Path


text = Path("answer.md").read_text(encoding="utf-8").lower()

assert "python" in text
assert any(token in text for token in ("news", "release", "event", "download", "psf"))
