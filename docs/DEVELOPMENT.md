# Development

## Requirements

- Node.js 22 or newer.
- Python 3.11 recommended.
- Windows is the primary packaged target.

## Install

```powershell
python -m pip install -e backend/
cd desktop
npm ci
```

## Run

```powershell
cd desktop
npm run dev
```

The Electron launcher starts the backend from the repository root with:

```powershell
python -m backend --mode web --port <free-port>
```

If the chosen Python does not already have the backend dependencies, the desktop
launcher will try to provision a managed environment at
`~/.metis/python-backend/venv` automatically and retry startup from there.

You can run the backend directly:

```powershell
python -m backend --mode web --port 5000
python -m backend --mode cli
```

## Validate

```powershell
python -c "import backend"
python -m pytest backend/tests/ -q
ruff check backend/ --select E,W,F --ignore E501,W291,W292,W293 --exclude backend/tools/coding
cd desktop
npm run typecheck
npm run test:contracts
npm run smoke:desktop
```

## Package

```powershell
cd desktop
npm run build-backend
npm run dist:win
```

PyInstaller output is written to `desktop/resources/backend-dist/` and is ignored.
