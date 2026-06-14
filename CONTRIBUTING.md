# Contributing

Thanks for helping improve Metis.

## Development Setup

1. Install Node.js 22 or newer.
2. Install Python 3.11 or newer.
3. Install backend dependencies:

```powershell
python -m pip install -e backend/
```

4. Install desktop dependencies:

```powershell
cd desktop
npm ci
```

5. Start the app:

```powershell
npm run dev
```

## Local Verification

Run the narrowest check that covers your change. For broad changes, run:

```powershell
python -m pytest backend/tests/ -q
cd desktop
npm run typecheck
npm run test:contracts
npm run smoke:desktop
```

For packaging changes:

```powershell
cd desktop
npm run build-backend
npm run dist:win
```

## Code Style

- Follow existing TypeScript, React, Electron, and Python patterns.
- Keep backend APIs localhost-only when they expose filesystem, terminal, or process operations.
- Do not commit generated outputs, logs, caches, local virtual environments, `.env`, keys, certificates, or installers.
- Prefer focused tests for behavior changes and contract tests for cross-process APIs.

## Pull Requests

1. Keep the PR focused.
2. Include a concise summary and verification commands.
3. Call out security, packaging, or migration risks explicitly.
4. Wait for CI to pass before requesting review.
