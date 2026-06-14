# Hermes Bridge

`backend.bridges` is the migration boundary introduced by NEW-16.

It exists so Metis can adapt stable Hermes runtime patterns without binding the
desktop app or Flask routes directly to the copied third-party snapshot in
`third_party/hermes-agent`.

Rules:

- Keep this package dependency-light.
- Do not call real LLM providers from bridge smoke tests.
- Do not import Hermes snapshot modules directly from UI or Flask routes.
- Add production behavior only in later NEW phases with focused tests.

Current files:

- `provider_contract.py` - provider config, result, error, and fake provider.
- `provider_profiles.py` - built-in provider profiles for fake, DeepSeek, OpenAI, OpenAI-compatible, Anthropic, and Gemini.
- `provider_registry.py` - provider id/alias resolution, backend kwargs normalization, and chat completions URL normalization.
- `provider_errors.py` - bridge-level provider error classification.
- `session_contract.py` - session/workspace record and store protocol.
- `tool_contract.py` - tool request/result and registry protocol.
- `tool_profiles.py` - toolset, safety, destructive, and approval metadata inference.
- `tool_registry_adapter.py` - adapter from Metis runtime registries to bridge `ToolProfile` objects.
- `event_contract.py` - stream event dataclasses for text, tools, errors, and done.
- `event_serializer.py` - stable `metis.agent_event.v1` payloads for Flask SSE with legacy frontend fields.
