---
name: debug-workflow
builtin: true
description: "修 bug、报错、失败测试、异常行为、页面空白、回归问题时使用。先复现，再定位，再最小修复，最后同路径验证。"
when_to_use: "用户说修复、bug、报错、不工作、空白、失败、回归、异常、找原因、定位问题。"
allowed-tools: [read_file, grep_search, glob_search, run_tests, execute_bash_command, robust_replace_in_file, write_file]
---
# Debug Workflow

Use this skill for bug fixes and runtime failures.

## Operating Rules

1. Reproduce or observe the failure before changing code whenever the repo gives you a practical path.
2. Reduce the problem to the smallest failing surface: one route, one component, one function, one test, or one command.
3. Read the exact files involved before editing. Do not patch from memory.
4. Prefer one narrow fix over broad rewrites.
5. Add or update a regression test when the bug is behavioral and testable.
6. Verify through the same path that exposed the bug, then run the smallest adjacent safety check.
7. If reproduction is impossible, state the assumption and verify a deterministic substitute.

## Debug Loop

1. Capture the symptom and expected behavior.
2. Inspect logs, stack traces, contracts, tests, and recent code paths.
3. Form one concrete hypothesis.
4. Test the hypothesis with a read, search, focused command, or minimal fixture.
5. Patch the smallest responsible code.
6. Run the failing check again.
7. Broaden verification only after the focused check is green.

## Stop Conditions

- Stop and ask only when required inputs are missing and guessing would risk data loss or unrelated rewrites.
- Do not declare success without naming the verification that passed.
