from pathlib import Path

text = Path("src/index.ts").read_text(encoding="utf-8")
assert "type User" in text or "interface User" in text
assert "parseUser(raw:" in text
assert "id: number" in text
assert "id: String(raw.id)" not in text
