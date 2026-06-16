## Project Operating Memory

- Default user-facing replies in this project should be Chinese unless the user asks otherwise.
- Treat `docs/dev-log/` and MVP construction notes as append-only logs. Add new dated sections instead of rewriting earlier construction history.
- Do not stage or commit local runtime state: `.metis/audit/`, `.metis/cache/`, `.metis/memory.json`, `.metis/project-profile.json`, `.agent_todos.json`, `NEWUPDATE.md`, logs, build output, or release installers unless the user explicitly asks for that artifact.
- Release handling preference: do not create a new GitHub release announcement unless explicitly requested. When asked to update the previous release, attach the new installer there.

## Project Profile

- Metis is a local desktop agent: Python backend plus Electron/React TypeScript desktop shell.
- Main dev command: `cd desktop && npm run dev`.
- Common checks: `python -m pytest`, `cd desktop && npm run typecheck`, `cd desktop && npm test`, `cd desktop && npm run test:contracts`.
- Common local preview port: `127.0.0.1:5174`, usually exposed through `METIS_DESKTOP_DEV_SERVER`.
- Keep stable project facts in `.metis/project-profile.json`; keep volatile learned/run state in `.metis/memory.json` and audit logs.
