# Architecture

Metis has three runtime layers:

```text
desktop/ Electron main + React renderer
  -> localhost HTTP/SSE bridge
backend/web Flask app
  -> agent events, sessions, settings, permissions
backend/runtime + backend/tools
  -> model providers, agent loop, tool registry, filesystem and terminal tools
```

## Desktop

`desktop/electron/` owns application windows, preload IPC, backend process launch, security defaults, diagnostics, and packaged backend discovery.

`desktop/src/` owns the workbench UI, settings, chat state, right rail previews, terminal panels, and API client.

## Backend

`backend/web/app.py` exposes localhost-only Flask routes and SSE streams.

`backend/runtime/` owns the agent loop, cancellation, provider backends, path safety, MCP integration, and tool registry adapter.

`backend/tools/` contains built-in coding, search, file, workflow, and desktop automation tools.

`backend/bridges/` contains small contracts for provider, tool, session, and event interoperability.

## Execution Profiles

Metis separates command execution from UI authority:

- `local_direct` runs commands in the source workspace on the host.
- `local_worktree` runs code sessions in isolated host worktrees and promotes diffs explicitly.
- `local_vm` runs commands in an isolated local runtime and returns stdout, diffs, and artifacts to the normal registry.

The first usable `local_vm` runner on Windows is `metis_wsl`, a Metis-managed WSL distro. It may show up in `hcsdiag` as a WSL2 utility VM, but it is not the HCS direct runner and Metis does not select `backend=hcs` for that path.

HCS direct remains a separate gated backend. It requires Metis-owned VM assets, a booted guest `metisd`, and a successful `runtime.hello` handshake over the HCS/vsock transport before it can be considered ready.

## Data Boundaries

Runtime state belongs in user-local directories such as `~/.metis/`, with legacy `.miro` paths read only where migration compatibility requires it. Build outputs, caches, logs, and local dev logs are ignored by default.
