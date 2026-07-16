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

The stable `local_vm` execution profile on Windows remains `metis_wsl`, a Metis-managed WSL distro. Copy-mode snapshots use a complete-or-fail policy: `max_files=0` and `max_bytes=0` mean the full selected workset, while explicit positive guards reject the session before execution instead of truncating it. Patch and diagnostics therefore never interpret omitted tail files as deletions.

HCS direct is a separate backend owned by the LocalSystem `metis-vm-svc`. It creates and starts the compute system, clones an immutable `sessiondata-template.vhdx` into a per-user/per-session writable disk, mounts it at `/data`, and communicates with guest `metisd` over HCS/HvSocket JSONL. The template is an ext4 filesystem labelled `METISDATA`; an unformatted VHDX is not a valid asset.

HCS readiness is evidence-gated. The production selftest must boot the guest, execute `runtime.hello`, run Python and an office-library round trip, mount `/data`, close the VM, and read the same random token from a new VM lifecycle. Its receipt is bound to the current kernel, initrd, rootfs, and session-data template fingerprint. Replacing any asset invalidates `hcs_direct_ready` until the selftest is repeated.

## Data Boundaries

Runtime state belongs in user-local directories such as `~/.metis/`, with legacy `.miro` paths read only where migration compatibility requires it. Build outputs, caches, logs, and local dev logs are ignored by default.
