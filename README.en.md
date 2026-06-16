<div align="center">

<img src="backend/assets/cover.png" alt="Metis" width="100%" />

# Metis · 墨提斯

**A local desktop AI agent that writes code, runs terminals, controls browsers, and operates Windows apps.**

> The wise stay quiet; the skilled never run dry.

![Electron](https://img.shields.io/badge/Electron-40-47848F?logo=electron&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-Flask%20%2B%20SSE-3776AB?logo=python&logoColor=white)
![i18n](https://img.shields.io/badge/i18n-中文%20%2F%20English-C9A24B)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial-C9A24B)

**Built by [linyeping](https://github.com/linyeping)**

**[中文](README.md) · English**

</div>

---

## What is Metis

**Metis** is a desktop AI workbench. It combines an Electron + React renderer with a local Python agent backend, and calls models through DeepSeek or any OpenAI-compatible API endpoint. Give it a goal and it plans steps, calls tools, observes results, and keeps going until the task is handled.

Metis is meant to do the work, not only talk about it:

- Read, search, and edit code, produce diffs, and run tests for verification.
- Control terminals, Git, the filesystem, local projects, and development servers.
- Use the right-side Preview Browser to inspect pages, click, type, screenshot, and collect console/network/DOM diagnostics.
- Use `/computer` to operate Windows desktop software and cross-application workflows.
- Show execution transparently through tool activity, task lists, permission gates, and audit logs.

Metis itself requires no account, no forced login, and no telemetry. Third-party connectors use standard OAuth, with tokens encrypted and stored locally.

---

## Current Core Capabilities

### Computer Use

`/computer` is designed for Windows desktop applications and cross-software workflows. The current implementation uses:

- **Win2 runtime**: prefers window-level Win32 automation and observes/acts relative to the target window, reducing full-screen coordinate drift.
- **Structured action loop**: `observe -> plan -> act -> verify`, with a fresh observation after meaningful actions.
- **Multi-source observation**: window screenshots, window inventory, accessibility/structured observation, and vision fallback when needed.
- **Desktop Expert**: complex desktop tasks are delegated to `desktop_expert`, which has desktop-specific tools and a longer turn budget.
- **Takeover overlay and activity cards**: active desktop control is visible, and chat tool cards show status, duration, and result.
- **Completion-state hardening**: if a tool result loses or mutates its call id, it is merged back into the running tool card; completed runs no longer leave a stale "desktop expert running" state.

### Preview Browser / Browser Use

`/browser` uses the right-side Preview Browser for local web and file automation:

- Automatically detects the active dev server, prefers `METIS_DESKTOP_DEV_SERVER`, and scans common ports such as `5173/5174/3000/4200/8000/8080`.
- Supports navigation, clicking, typing, DOM observation, screenshots, reloads, and reuse of the right rail's current URL.
- Captures console errors/warnings, failed network requests, page JavaScript exceptions, DOM summaries, title, URL, viewport, and screenshot evidence.
- Includes a Browser Verifier for element existence/visibility, clickable buttons, writable inputs, blank-page checks, console-error checks, and pure white/black screenshot checks.
- Browser Activity is collapsed by default so it does not squeeze the live page preview.

### OAuth and Connectors

Metis now has a connector framework and OAuth foundation:

- The Electron main process handles OAuth callbacks, encrypted token storage, and security boundaries.
- The Settings UI includes connector entry points for future Gmail, GitHub, and similar integrations.
- Tokens are not written to logs, not injected into model context, and not routed through a relay service.

### Context Engineering and Long-Run Stability

- Automatic context compaction preserves task summaries and recoverable boundaries.
- Tool results can be compacted so long outputs do not consume the entire model context.
- Run recovery, background run tracking, heartbeat reconnects, and session checkpoints are built in.
- Tool contracts use a unified SSE event contract shared by backend and frontend.

---

## Feature Overview

<div align="center">
<img src="backend/assets/Feature%20Showcase.png" alt="Feature Showcase" width="100%" />
</div>

| Module | Description |
|---|---|
| Agent loop | Planning, tool calls, observation, continuation, truncation recovery, deferred tool activation, and run recovery |
| Code tools | File read/write, code search, semantic index, AST/patch edits, test execution, and diff preview |
| Terminal tools | Local command execution, environment diagnostics, build and test debugging |
| Browser Use | Right-side Preview Browser automation, DOM/screenshot/console/network diagnostics, and verification |
| Computer Use | Win2 desktop automation, window observation, mouse/keyboard execution, vision fallback, Desktop Expert |
| Task lists | Live plan, active steps, and completion state |
| Tool activity | Per-tool status, summary, duration, error hints, and expandable details |
| Permission modes | Ask for approval, approve on my behalf, or full access, with risk-based gating |
| OAuth connectors | Local OAuth callback, encrypted token storage, and groundwork for Gmail/GitHub integrations |
| Internationalization | Chinese / English UI and documentation |
| Packaging | PyInstaller backend bundle plus electron-builder Windows installer |

---

## Architecture

<div align="center">
<img src="backend/assets/Architecture.png" alt="Architecture" width="100%" />
</div>

```text
Metis Desktop
├─ Electron main process
│  ├─ Windowing, menus, OAuth, WebContentsView Preview
│  ├─ Backend lifecycle management
│  └─ Windows packaging entry
├─ React renderer
│  ├─ Chat / Tool Activity / Right Rail / Settings
│  ├─ Browser Activity / Preview Browser UI
│  └─ Zustand stores + assistant-ui message stream
└─ Python backend
   ├─ Flask + SSE API
   ├─ agent_loop / tool_registry / skills
   ├─ browser automation / desktop automation
   ├─ provider adapters
   └─ checkpoint / context budget / connectors
```

Communication model:

- The renderer talks to the backend over HTTP / SSE.
- The Electron main process owns local preview, OAuth, packaged backend startup, and desktop shell capabilities.
- Backend tools interact with the local filesystem, terminal, browser, desktop automation, and model APIs.

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 64-bit |
| Node.js | Required for development mode; not required for installed users |
| Python | Required for development mode; bundled into the packaged backend |
| Network | Required for model API calls |
| API key | DeepSeek or any OpenAI-compatible endpoint |
| Desktop control | `/computer` controls mouse and keyboard; sensitive actions should be confirmed by the user |

This build is not code-signed yet, so Windows SmartScreen may warn before launch.

---

## Development

```powershell
python -m pip install -e backend/

cd desktop
npm ci
npm run dev
```

Development mode starts:

- Vite renderer: `http://127.0.0.1:5174` by default
- Electron desktop shell
- Local Python backend managed by the Electron launcher

---

## Common Commands

```powershell
# Frontend type check
cd desktop
npm run typecheck

# Frontend unit tests
npm run test

# Electron / security / contract tests
npm run test:contracts

# Backend tests
cd ..
python -m pytest backend/tests/ -q

# Production renderer build
cd desktop
npm run build
```

---

## Build a Windows EXE

```powershell
cd desktop
npm run dist:win
```

`dist:win` runs:

1. `npm run build-backend`: bundles the Python backend with PyInstaller.
2. `npm run build`: builds the React/Vite renderer.
3. `electron-builder --win nsis`: produces a Windows NSIS installer.

Output location:

```text
desktop/release/
```

To verify only the production renderer build:

```powershell
cd desktop
npm run build
```

---

## Project Structure

```text
Miro/
├── backend/
│   ├── bridges/        # event contracts and provider/tool protocol bridges
│   ├── runtime/        # agent loop, tool registry, skills, checkpoint, context budget
│   ├── tools/          # code, browser, desktop, retrieval, and other tools
│   ├── web/            # Flask API, SSE, Preview Browser bridge
│   └── assets/         # cover, architecture, and feature showcase images
├── desktop/
│   ├── electron/       # Electron main/preload, OAuth, packaging entry
│   ├── src/            # React UI, stores, runtime, i18n
│   └── scripts/        # build, contract, and smoke scripts
├── docs/               # development logs and design documents
└── README.md / README.en.md
```

---

## Privacy and Safety

- Metis does not require a platform account and does not include built-in telemetry.
- API keys and OAuth tokens stay in local configuration/encrypted storage.
- Connector tokens are not inserted into model context.
- Tool actions are audited for traceability.
- `/computer` and `/browser` distinguish reading information from sending or submitting data; external side effects, sensitive data, deletion, uploads, and authorization changes should be confirmed first.

---

## License

**[PolyForm Noncommercial 1.0.0](LICENSE)** © 2026 linyeping

Source-available, **free for personal / non-commercial use** including learning, research, personal projects, and nonprofits.
**Any commercial use or commercial derivative work requires prior written paid authorization from the author**.

---

<div align="center">

**Built by [linyeping](https://github.com/linyeping)** · The wise stay quiet; the skilled never run dry.

</div>
