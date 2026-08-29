# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning after the first public release.

## [26.8.29] - 2026-08-29

## 主要更新

### 1. 客户端身份标志

模型请求现在会携带明确的客户端标志：

- 桌面端：`MetisDesktop/version`
- CLI：`MetisCLI/version`

### 2. 桌面端更新流程修复

修复点击更新后应用退出但无法重新启动的问题：

- 安装更新前等待 Metis Python 后端完全退出
- 增加更新安装阶段日志
- 捕获安装器启动异常
- 改善更新失败时的错误返回

### 3. 版本信息

- Desktop：`26.8.29`
- CLI：`26.8.29`
- 更新日期：2026-08-29

## 安装说明

如果旧版本更新后无法启动，请重新运行安装包并选择原安装目录。

## [26.7.27] - 2026-07-27

### Added

- Added Thinking Orbs for task understanding, search, tool execution, and response composition, driven by real runtime events.
- Added inline assistant-turn status copy with turn, step, elapsed-time, and tool-jump details.

### Changed

- Moved active runtime status from the composer dock into the current assistant response.
- Matched 20px inline animation pacing to the upstream 64px demo while retaining the compact preset geometry.
- Kept queued follow-ups and background subagents near the composer as distinct cross-turn state.

### Fixed

- Removed the transient heartbeat activity icon shown during preparation, retries, reconnects, and failure transitions.

## [26.7.17] - 2026-07-17

### Added

- Added native Metis Design with project management, conversational Studio, live preview, design systems, and bundled HTML/PDF/PPTX/image export.
- Added task-completion notifications, unread and archived sessions, custom desktop pets, and Design task-state integration.
- Added HCS direct-runner session persistence, production guest handshake verification, and bundle-bound boot readiness receipts.

### Changed

- Unified Design with Metis theme, locale, model configuration, notifications, navigation, and desktop-pet state.
- Improved Chat, Cowork, Code, and Design switching, tray restoration, transparent pet rendering, and long-session menus.
- Changed isolated workspace snapshots to complete-or-fail semantics with unlimited defaults instead of silent 2,000-file / 80 MB truncation.

### Security

- Tightened the privileged VM service boundary and require persistent-disk mount and guest-protocol evidence before HCS readiness is promoted.

## [26.7.11] - 2026-07-11

### Added

- Added configurable close-window behavior: ask, minimize to tray, or quit.
- Added detailed localized Store descriptions and colored connector logos.

### Changed

- Replaced the desktop, tray, and installer icon with the rounded flower mark.
- Improved Chat, Cowork, and Code switching to avoid duplicate loads and stale session state.

## [3.0.0] - Unreleased

### Added

- Electron desktop shell with React, Vite, and @assistant-ui renderer.
- Flask SSE backend bridge for chat, run events, sessions, workspaces, memory, cron, permissions, and diagnostics.
- Multi-provider LLM setup for OpenAI, DeepSeek, Anthropic, Gemini, DashScope, OpenRouter, Groq, Mistral, and custom OpenAI-compatible endpoints.
- Run registry, background activity, cancellation, recovery snapshots, and context compaction.
- Right-rail workbench for file preview, diff review, terminal sessions, web preview, tool activity, and diagnostics.
- Permission rules, approval flow, and audit logs.
- PyInstaller backend packaging and Windows electron-builder target.

### Changed

- Project metadata is prepared for MIT-licensed open-source distribution.
- Boot diagnostics surface backend startup progress, failures, retry, and log access.

### Security

- Added sensitive data handling guidance and default ignore rules for local secrets, build outputs, and diagnostics.
