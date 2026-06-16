<div align="center">

<img src="backend/assets/cover.png" alt="Metis" width="100%" />

# Metis · 墨提斯

**A desktop AI agent that reads your code, runs your terminal, and drives the web.**

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

## What is it

**Metis** is a desktop AI agent client: an Electron + React front end backed by a Python agent process running on your machine. Give it a goal and it plans, calls tools, and works through the task step by step — reading and writing code, running terminal commands, querying databases, browsing the web, and even taking over the desktop when needed. Every action streams into the workbench on the right, so you can see exactly what it's doing and what it changed.

Models are accessed over an API: DeepSeek is supported out of the box, and any OpenAI-compatible endpoint (including custom relays) works too — just drop your key into Settings.

A few deliberate choices:

- 🔒 **It doesn't collect your stuff** — Metis itself requires no account, no forced login, and no telemetry; third-party connectors use standard OAuth, with tokens encrypted locally, never leaving your machine and never passing through a relay.
- 🌏 **Bilingual** — switch the entire UI between 中文 and English in one click.
- 🧱 **Built to stay up** — crash self-recovery, health-heartbeat reconnection, and an action audit log, so it tries not to fall over mid-task.

Still actively polishing — try it out and file issues.

---

## Features

<div align="center">
<img src="backend/assets/Feature%20Showcase.png" alt="Feature Showcase" width="100%" />
</div>

| Module | What it does |
|---|---|
| 🤖 **Agent loop** | Plan → call tool → observe → continue; supports **auto-continuation after truncation**, deferred tool activation, action auditing |
| 🛠️ **Toolbox** | Code read/write/search, **local semantic index (RAG)**, terminal, Git/Diff, file preview |
| 🌐 **Browser control** | `/browser`: autonomous browsing via the DOM, form filling, reusing logged-in sessions |
| 🖥️ **Desktop control** | `/computer`: screenshot + coordinate-based control of any native app, configurable coordinate space |
| ⚡ **Parallel sub-agents** | Fan out read-only analysis tasks in parallel to speed up large-repo understanding |
| 📋 **Audit log** | Every tool action per turn is written to `.metis/audit/` for traceability |
| 🎚️ **Permission modes** | Ask for approval / approve on my behalf / full access, graded by risk |
| ⏱️ **Scheduled tasks** | Built-in cron to run agent workflows on a schedule |
| 🧩 **Skills & `/` commands** | Type `/` for the command palette: `/new`, `/compact`, `/rewind`, `/browser`, `/computer`, plus custom skills |
| 🎨 **16 themes** | 8 light + 8 dark, gold-forward palette; light/dark dual mode each remembers its own theme |
| 🔁 **Self-healing reconnect** | Auto-restart on backend crash, 8s heartbeat probe for a stalled API, honest "reconnecting" status |
| 📦 **One-step packaging** | PyInstaller bundles the backend + electron-builder produces a ready-to-run Windows installer |

---

## Architecture

<div align="center">
<img src="backend/assets/Architecture.png" alt="Architecture" width="100%" />
</div>

- **Renderer** `desktop/src/` — React 19 + Vite + Zustand state + assistant-ui message stream.
- **Main process** `desktop/electron/main.cjs` — window management, native `WebContentsView` web preview, packaging entry.
- **Backend** `backend/` — Flask/SSE service, the `agent_loop`, the `tool_registry`, and provider adapters.
- The three communicate over **HTTP / SSE**, with tools ultimately talking to **DeepSeek / any compatible endpoint**.

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Disk | ~450 MB |
| Network | Internet access to call the model API |
| API key | A DeepSeek key, or any OpenAI-compatible endpoint key, entered in the first-run wizard |
| Browser | `/browser` reuses the system Chrome / Edge for web control |

The installer ships with the runtime bundled — no separate dev environment to set up. `/computer` desktop control moves the mouse and keyboard and may trigger a system authorization prompt on first use. This build is not yet code-signed, so Windows SmartScreen may warn; you can choose to continue.

---

## Development

```powershell
python -m pip install -e backend/   # install the backend
cd desktop
npm ci
npm run dev                          # dev mode, auto-starts the backend
```

## Verification

```powershell
python -m pytest backend/tests/ -q   # backend unit tests
cd desktop
npm run typecheck                     # type check
npm run test                          # renderer unit tests (vitest)
npm run test:contracts                # contract / security tests
```

## Packaging (Windows)

```powershell
cd desktop
npm run build-backend                 # PyInstaller bundles the backend
npm run dist:win                      # produces an NSIS installer → desktop/release/
```

---

## Project structure

```
Miro/
├── backend/          # Python agent: Flask/SSE, agent_loop, tools, provider adapters
│   ├── runtime/      #   agent loop, tool registry, skills, audit, parallel sub-agents
│   ├── tools/        #   code / browser / desktop / retrieval tool implementations
│   └── assets/       #   brand imagery (cover / architecture / feature showcase)
├── desktop/          # Electron desktop app
│   ├── electron/     #   main process, preload, security / contract tests
│   ├── src/          #   React renderer (components, stores, runtime, i18n)
│   └── scripts/      #   packaging & smoke scripts
└── docs/             # architecture / development / changelog
```

## License

**[PolyForm Noncommercial 1.0.0](LICENSE)** © 2026 linyeping

Source-available, **free for personal / non-commercial use** (learning, research, personal projects, nonprofits).
**Any commercial use or commercial derivative work requires prior written (paid) authorization from the author** — see the repository homepage for contact.

---

<div align="center">

**Built by [linyeping](https://github.com/linyeping)** · The wise stay quiet; the skilled never run dry.

</div>
