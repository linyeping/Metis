---
name: git-discipline
builtin: true
description: "git 提交、分支、diff 自查、commit message、发布前整理时使用。强调原子提交和不回滚用户改动。"
when_to_use: "用户说提交、commit、分支、diff、git status、PR、发布、回滚、撤销。"
disable-model-invocation: false
allowed-tools: [check_git_status, git_diff, execute_bash_command]
---
# Git Discipline

Use this skill when preparing or reviewing version control changes.

## Rules

1. Inspect status before staging or committing.
2. Separate unrelated work into separate commits when possible.
3. Never revert user changes unless the user explicitly asked.
4. Read the diff before summarizing or committing.
5. Do not include generated caches, local data, build outputs, secrets, or machine-specific paths.
6. Use clear commit messages: imperative subject, concise scope, behavior-focused body when needed.

## Pre-Commit Checklist

- Focused tests or checks passed.
- Diff matches the requested scope.
- No accidental formatting churn.
- No debug prints, temporary scripts, or credentials.
- User-visible behavior is summarized plainly.
