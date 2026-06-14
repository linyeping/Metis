---
name: frontend-app
builtin: true
description: "写网页、前端应用、React/Vite/Next/Vue、UI 修复、页面预览、自适应布局时使用。完成后必须启动本地预览并自测。"
when_to_use: "用户说网页、前端、页面、UI、React、Vite、Next、Vue、CSS、响应式、localhost、预览、学生管理系统。"
allowed-tools: [read_file, grep_search, glob_search, execute_bash_command, run_tests, robust_replace_in_file, write_file]
paths: ["**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.css", "**/*.html", "**/package.json"]
---
# Frontend App

Use this skill for frontend applications and UI work.

## Build Rules

1. Inspect the existing framework, routes, styling system, and package scripts before editing.
2. Build the actual usable screen first, not a marketing placeholder.
3. Keep controls complete: loading, empty, error, disabled, hover/focus, and narrow viewport states.
4. Keep layout stable with explicit dimensions, grid constraints, and responsive breakpoints.
5. Do not let text overlap or escape buttons/cards on mobile or desktop.
6. Use existing component and CSS conventions before introducing new patterns.
7. Prefer real browser verification for visual changes.

## Verification Rules

1. Run the project’s typecheck/test/build command when available.
2. Start the local dev server or preview command after implementation.
3. Open the app at localhost and verify the changed workflow renders with styles.
4. For generated HTML/CSS/JS files, verify CSS and JS load through the preview route.
5. Stop temporary dev processes unless the user asked to keep them running.

## Final Report

Name the local URL or command used for preview, plus the verification commands that passed.
