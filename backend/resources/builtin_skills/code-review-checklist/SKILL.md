---
name: code-review-checklist
builtin: true
description: "代码审查、提交前自查、PR review、风险检查时使用。关注安全、回归、错误处理、测试缺口。"
when_to_use: "用户说 review、审查、检查、提交前、PR、风险、有没有问题。"
---
# Code Review Checklist

Use this skill for review mode and pre-submit self-checks.

## Review Priorities

1. Correctness: broken behavior, wrong edge cases, stale assumptions, race conditions.
2. Security: path traversal, command injection, secret leakage, unsafe URLs, permission bypass.
3. Data integrity: destructive operations, migration risks, persistence format changes.
4. Error handling: swallowed exceptions, unclear user errors, partial failure states.
5. Performance: accidental unbounded scans, repeated expensive work, N+1 calls, large context growth.
6. Compatibility: public API shape, existing tests, packaging, platform differences.
7. Tests: missing regression coverage for changed behavior.

## Review Style

- Findings first, ordered by severity.
- Cite exact files and lines when available.
- Avoid style-only comments unless they hide a real maintenance risk.
- If no issues are found, say that clearly and name remaining residual risk.

## Self-Review Before Final

Check the diff, run focused verification, and ensure no unrelated edits slipped in.
