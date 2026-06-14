# Metis Desktop

Electron + React desktop shell for Metis.

## Responsibilities

- Launch and monitor the local Python backend.
- Render the chat workbench, settings, right rail previews, terminal panels, and diagnostics.
- Provide hardened Electron preload IPC for the renderer.
- Run desktop contract, smoke, and performance checks.
- Build Windows installers with electron-builder.

## Commands

```powershell
cd desktop
npm ci
npm run dev
npm run typecheck
npm run test:contracts
npm run smoke:desktop
```

Packaging:

```powershell
cd desktop
npm run build-backend
npm run dist:win
```

## Structure

```text
desktop/
  electron/      Main process, preload, backend launcher, security helpers
  src/           React renderer, stores, hooks, runtime helpers
  scripts/       Build, smoke, performance, and contract scripts
  resources/     Icons and generated packaged backend output
```

The backend source lives in `../backend/` and is started in development with `python -m backend`.
