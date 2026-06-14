# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning after the first public release.

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
