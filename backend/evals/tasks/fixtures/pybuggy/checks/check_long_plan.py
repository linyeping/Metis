from pathlib import Path

required = [
    Path("docs/roadmap.md"),
    Path("pybuggy/features.py"),
    Path("tests/test_features.py"),
]
for path in required:
    assert path.is_file(), f"missing {path}"
assert "feature_flags" in Path("pybuggy/features.py").read_text(encoding="utf-8")
assert "Phase 1" in Path("docs/roadmap.md").read_text(encoding="utf-8")
