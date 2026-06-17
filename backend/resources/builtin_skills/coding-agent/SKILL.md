---
name: coding-agent
builtin: true
description: "代码开发、跨文件修改、工程化实现、测试修复、重构和提交前收束时使用。优先后台读写代码和运行测试，不默认接管桌面。"
when_to_use: "用户说写代码、实现功能、改项目、修测试、工程化、重构、代码开发、提交前整理、构建失败。"
allowed-tools: [read_file, grep_search, glob_search, execute_bash_command, run_tests, robust_replace_in_file, write_file, load_skill]
disallowed-tools: [desktop_action, desktop_win2_task]
---
# Coding Agent

Use this skill for serious code development work inside a repository.

## Default Route

1. Prefer repository tools over desktop control.
2. Use Computer Use only when the user explicitly asks to operate a GUI, the needed state exists only on screen, or a real desktop verification is required.
3. Start from the current workspace, project profile, repo map, tests, and existing patterns.
4. Load narrower skills when they match the task, such as `python-project`, `frontend-app`, `debug-workflow`, `tdd-workflow`, `git-discipline`, or `code-review-checklist`.

## Context Discipline

1. Read before editing. Do not propose or patch code you have not inspected.
2. Search narrowly with `grep_search` / `glob_search`; avoid dumping huge trees into context.
3. Use the repo map as orientation, then open the exact files that matter.
4. Keep high-signal notes in the response or project memory when tool results may be compacted.
5. When output is large, keep signatures, filenames, failing lines, and tail output; drop repeated noise.

## Implementation Loop

1. Identify the behavior contract and the smallest affected surface.
2. Add or update a focused test first when the change is behavioral and testable.
3. Make the smallest coherent code change.
4. Run the focused test or typecheck that proves the change.
5. Run adjacent regression checks when touching shared runtime, permissions, providers, browser/computer-use, artifacts, or packaging.
6. Inspect the diff and remove temporary code, debug prints, duplicated branches, and unused abstractions.

## Coding Standards

1. Follow existing architecture, naming, error handling, and UI patterns.
2. Prefer structured parsers and typed payloads over ad hoc string parsing.
3. Keep platform behavior explicit on Windows paths, shell commands, filesystem roots, and process execution.
4. Validate at system boundaries: user input, external APIs, file paths, model/tool payloads, and serialized data.
5. Do not add speculative abstractions or compatibility shims unless the current behavior requires them.

## Verification Gate

For non-trivial changes, finish with evidence:

1. Focused test or reproduction command.
2. Broader regression command when shared surfaces changed.
3. Diff review for unrelated edits.
4. A short risk note if something could not be verified.

Use the fixed regression runner for release-sensitive surfaces:

```powershell
cd desktop
npm run test:fixed-regression
```

Run a subset while iterating:

```powershell
cd desktop
npm run test:fixed-regression -- --suite permissions,model-tools
```

