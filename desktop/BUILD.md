# Metis Desktop Build Notes

## Windows installer

Build the backend bundle and NSIS installer from `desktop/`:

```powershell
$env:METIS_PYTHON="D:\Anaconda3\python.exe"
npm run dist:win
```

The NSIS installer allows changing the installation directory and installs per user (`perMachine: false`), so admin rights are not required.

## Windows Defender EPERM during packaging

If `electron-builder` fails while renaming `win-unpacked.tmp` to `win-unpacked` with `EPERM`, add a Windows Security exclusion for the release output directory before building:

```text
Windows Security -> Virus & threat protection -> Manage settings -> Exclusions
Add folder: D:\pycharm\py.project\Miro\desktop\release
```

Then rerun:

```powershell
$env:METIS_PYTHON="D:\Anaconda3\python.exe"
npm run dist:win
```

## Data directory rules

Electron resolves one shared data root, then splits it into:

- `data\metis`: backend `METIS_HOME` for `METIS.md`, sessions, config, logs, skills, plugins, MCP, tools, cron, and SQLite state.
- `data\electron`: Electron `userData` for diagnostics and preview evidence.

Resolution order:

1. `METIS_DATA_ROOT` environment variable.
2. `data-root.json` next to the installed executable or dev `desktop/` folder.
3. `<install-dir>\data` when writable.
4. `%LOCALAPPDATA%\Metis\data` if the install directory is not writable.
5. `~\.metis-desktop\data` as a last-resort fallback.

`METIS_HOME` can still override only the backend home. When unset, Electron injects `METIS_HOME=<data-root>\metis` into the backend process.

Example `data-root.json`:

```json
{
  "dataRoot": "D:\\MetisData"
}
```

Relative values are resolved relative to `data-root.json`.

## Legacy data

On packaged startup, if the new `METIS_HOME` is empty and old `~\.metis` exists, Metis copies core state into the new location once:

- `session-state.db`, `sessions`, `workspaces`
- `config.json`, `METIS.md`
- `skills`, `plugins`
- `mcp.json`, `tools.json`, `cron.json`

Logs and managed Python caches are not migrated. Set `METIS_SKIP_LEGACY_MIGRATION=1` to skip this automatic copy. In development mode, automatic migration is disabled unless `METIS_MIGRATE_LEGACY_HOME=1` is set.
