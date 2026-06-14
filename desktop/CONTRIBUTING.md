# Desktop Contributing

Desktop development happens from `desktop/`.

```powershell
cd desktop
npm ci
npm run dev
npm run typecheck
npm run test:contracts
npm run smoke:desktop
```

Backend integration checks should be run from the repository root:

```powershell
python -m pip install -e backend/
python -m pytest backend/tests/ -q
```

For full project guidance, see `../CONTRIBUTING.md`.
