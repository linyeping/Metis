---
name: python-project
builtin: true
description: "Python 工程、pytest、依赖、虚拟环境、类型标注、Flask/FastAPI/脚本修复时使用。"
when_to_use: "用户说 Python、pytest、pip、venv、Flask、FastAPI、脚本、模块、导入错误、类型标注。"
allowed-tools: [read_file, grep_search, glob_search, execute_bash_command, run_tests, robust_replace_in_file, write_file]
paths: ["**/*.py", "**/pyproject.toml", "**/requirements*.txt"]
---
# Python Project

Use this skill for Python codebases.

## Project Rules

1. Inspect `pyproject.toml`, requirements files, test layout, and import style before editing.
2. Keep imports explicit and avoid hidden path hacks unless the project already uses them.
3. Preserve public function signatures unless the task requires changing them.
4. Prefer small pure functions for parsing, validation, and serialization logic.
5. Use pathlib/os path handling carefully on Windows and POSIX.
6. Avoid adding heavyweight dependencies for simple parsing or formatting.

## Testing Rules

1. Add focused pytest coverage for bug fixes and parsing/path/security behavior.
2. Run the smallest relevant pytest target first.
3. Run broader backend tests when touching shared runtime, routes, or configuration.
4. If a dependency is optional, tests should skip or fallback cleanly.

## Packaging Rules

Keep runtime dependencies lean. Anything used by packaged desktop startup must be listed intentionally.
