# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning after the first public release.

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
