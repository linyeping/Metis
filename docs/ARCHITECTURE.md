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

## Data Boundaries

Runtime state belongs in user-local directories such as `~/.metis/`, with legacy `.miro` paths read only where migration compatibility requires it. Build outputs, caches, logs, and local dev logs are ignored by default.
