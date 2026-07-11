<div align="center">

<img src="backend/assets/cover.png" alt="Metis" width="100%" />

# Metis · 墨提斯

**Connect models to code, terminals, browsers, and Windows, then carry a goal through to verifiable results.**

<p>
  <img alt="Electron 40" src="https://img.shields.io/badge/Electron-40-47848F?style=flat-square&logo=electron&logoColor=white" />
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=111111" />
  <img alt="TypeScript 6" src="https://img.shields.io/badge/TypeScript-6-3178C6?style=flat-square&logo=typescript&logoColor=white" />
  <img alt="Python 3.11" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img alt="Flask SSE" src="https://img.shields.io/badge/Flask-SSE-2E8B72?style=flat-square&logo=flask&logoColor=white" />
</p>

<p>
  <a href="https://github.com/linyeping/Metis/releases/download/v26.7.11/Metis-Setup-26.7.11.exe"><img alt="Download Metis 26.7.11" src="https://img.shields.io/badge/Download_Metis-26.7.11-357EC7?style=for-the-badge&logo=windows11&logoColor=white" /></a>
  <a href="https://github.com/linyeping/Metis/releases/tag/v26.7.11"><img alt="View Release Notes" src="https://img.shields.io/badge/View-Release_Notes-2E8B72?style=for-the-badge&logo=github&logoColor=white" /></a>
</p>

<sub>Windows 10 / 11 · 64-bit · DeepSeek / OpenAI-compatible API</sub><br />
<sub><a href="README.md">中文</a> · Built by <a href="https://github.com/linyeping">linyeping</a> · Product direction and design acknowledgements: <a href="https://github.com/Serein0812">Serein</a></sub>

</div>

<br />

<h2 align="center">One Workbench, Three Levels of Execution</h2>

<p align="center">
  Each task enters the surface that fits it best while sharing the same sessions, tools, permissions, and evidence chain.
</p>

<p align="center">
  <img src="backend/assets/work-surfaces.png" alt="Metis Chat, Cowork, and Code work surfaces" width="100%" />
</p>

<p align="center">
  <b>CHAT</b> fast understanding and light execution &nbsp;·&nbsp;
  <b>COWORK</b> plan-driven multi-step delivery &nbsp;·&nbsp;
  <b>CODE</b> repository-centered engineering loop
</p>

<p align="center"><sub>Sessions, workspaces, and drafts stay isolated by mode, so rapid navigation cannot let stale requests overwrite the active record.</sub></p>

<br />

<h2 align="center">Not Just Answers. Finished Work.</h2>

<p align="center">
  Metis brings model reasoning, project context, tool calls, permission control, and verification into one local workflow.
</p>

<div align="center">
  <img src="backend/assets/Feature%20Showcase.png" alt="Metis executes code, browser, desktop, and skill workflows" width="100%" />
</div>

<table align="center" width="100%">
  <tr>
    <td width="50%" valign="top">
      <b>From intent to evidence</b><br /><br />
      Understand the repository and context → build a plan → edit files and run commands → inspect web or Windows apps → collect diffs, tests, screenshots, logs, and artifacts.
    </td>
    <td width="50%" valign="top">
      <b>Long-running continuity</b><br /><br />
      Context compaction, checkpoints, background runs, reconnect, and resume keep work moving while progress and final artifacts remain inspectable.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <b>Local-first</b><br /><br />
      Filesystem, terminal, browser, and desktop tools run on the user's device, with no platform account requirement and no built-in telemetry.
    </td>
    <td width="50%" valign="top">
      <b>Verifiable delivery</b><br /><br />
      Tool calls, duration, summaries, and results stay visible. Completion is backed by tests, diffs, screenshots, or logs instead of a bare “done.”
    </td>
  </tr>
</table>

<br />

<h2 align="center">Connect the Services You Already Use</h2>

<p align="center">
  <img src="desktop/src/assets/connectors/github.svg" alt="GitHub" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/slack.svg" alt="Slack" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/notion.svg" alt="Notion" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/google-calendar.svg" alt="Google Calendar" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/google-drive.svg" alt="Google Drive" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/gmail.svg" alt="Gmail" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/postgresql.svg" alt="PostgreSQL" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/filesystem.svg" alt="Local Filesystem" width="42" height="42" />
</p>

<p align="center"><sub>The Store and connector center expose concrete capabilities, provenance, authorization, available tools, and live connection state.</sub></p>

<br />

<details>
  <summary><b>What's new in 26.7.11</b></summary>
  <br />
  <ul>
    <li>Rolled out the rounded flower identity across the app, tray, Store, and installer.</li>
    <li>Added colored connector branding and searchable capability descriptions.</li>
    <li>Added ask, minimize-to-tray, and quit behaviors for closing the window.</li>
    <li>Reworked Chat / Cowork / Code navigation to prevent duplicate loads and stale-session races.</li>
  </ul>
</details>

<br />

---

<h2 align="center">Local Architecture</h2>

<div align="center">
  <img src="backend/assets/Architecture.png" alt="Metis local architecture" width="100%" />
</div>

<table align="center" width="100%">
  <tr>
    <td width="33%" valign="top"><b>Electron Desktop</b><br /><sub>Windowing, menus, OAuth, WebContentsView Preview, and packaged backend lifecycle.</sub></td>
    <td width="34%" valign="top"><b>React Workbench</b><br /><sub>Chat / Cowork / Code, tool activity, right workbench, settings, and state management.</sub></td>
    <td width="33%" valign="top"><b>Python Agent</b><br /><sub>Flask + SSE, agent loop, tool registry, skills, browser and desktop automation, checkpoints, and connectors.</sub></td>
  </tr>
</table>

> [!NOTE]
> The renderer talks to the local backend over HTTP / SSE. API keys and OAuth tokens never pass through a Metis relay and stay out of logs and model context.

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

**Built by [linyeping](https://github.com/linyeping)** · Product direction and part of the design thinking: [Serein](https://github.com/Serein0812) · The wise stay quiet; the skilled never run dry.

</div>
