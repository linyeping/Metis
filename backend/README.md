# Metis Backend

Python Flask/SSE backend and agent runtime for Metis.

## Responsibilities

- Serve localhost HTTP routes and SSE agent streams.
- Manage sessions, workspaces, settings, provider state, permissions, and diagnostics.
- Run the agent loop and model provider adapters.
- Register built-in coding, filesystem, terminal, search, workflow, and desktop automation tools.

## Install

```powershell
python -m pip install -e backend/
```

## Run

```powershell
python -m backend --mode web --port 5000
python -m backend --mode cli
```

The desktop app launches the web mode automatically during development and packaged runs.

## Validate

```powershell
python -c "import backend"
python -m pytest backend/tests/ -q
ruff check backend/ --select E,W,F --ignore E501,W291,W292,W293 --exclude backend/tools/coding
```

## Structure

```text
backend/
  web/        Flask app, sessions, settings, runtime state
  runtime/    Agent loop, providers, cancellation, path safety, MCP, registry
  tools/      Built-in tools
  core/       Prompts, constants, context and memory helpers
  bridges/    Provider, tool, session, and event contracts
  tests/      pytest coverage
```
