---
name: tdd-workflow
builtin: true
description: "新功能、重构、行为变更、复杂逻辑实现时使用。先写失败测试，再实现，再绿灯重构。"
when_to_use: "用户说新增功能、实现、重构、补测试、测试驱动、边界用例、验收标准。"
allowed-tools: [read_file, grep_search, glob_search, run_tests, execute_bash_command, robust_replace_in_file, write_file]
---
# TDD Workflow

Use this skill when a change has clear behavior that can be tested.

## Operating Rules

1. Identify the behavior contract before writing production code.
2. Add the smallest failing test that proves the missing behavior or regression.
3. Run the focused test and confirm it fails for the expected reason.
4. Implement the smallest code change that makes the test pass.
5. Run the focused test again.
6. Refactor only after green, and keep the test green.
7. Add boundary cases when the feature touches parsing, permissions, paths, serialization, concurrency, or user-visible state.

## Test Shape

- Name tests after behavior, not implementation.
- Prefer local unit tests for pure logic.
- Use contract tests for prompt/tool/schema wiring.
- Use integration tests when behavior crosses backend/frontend/process boundaries.

## Reporting

End with the tests run and the behavior each one protects.
