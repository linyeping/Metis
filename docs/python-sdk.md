# Metis Python SDK

The Python SDK is the in-process form of Metis headless execution. It imports the same agent loop, model routing, tools, permission engine, session database, and `metis.agent_event.v1` serializer used by desktop and CLI. It does not start `metis.exe`, parse terminal output, or maintain a second event protocol.

## Install

Stable releases are published as `metis-agent-sdk` while the public import remains `metis`:

```powershell
python -m pip install metis-agent-sdk
```

Install the latest source revision with:

```powershell
python -m pip install "git+https://github.com/linyeping/Metis.git#subdirectory=backend"
```

For repository development:

```powershell
python -m pip install -e backend/
```

The distribution name is `metis-agent-sdk`; the public import is `metis`.

## Version and compatibility

Metis uses the product version for package releases (for example `26.7.27`) and a separate stable API level exposed as `metis.SDK_API_VERSION`. API level `1` covers the public names exported by `metis`, the `Agent` constructor and run methods, immutable `AgentEvent` mapping behavior, `AgentResult`, and the `metis.agent_event.v1` envelope.

Within API level 1, patch and product-version updates may add optional parameters, event fields, or event kinds, but do not remove public names or change existing field meaning without a deprecation cycle. Runtime tools and provider behavior are capabilities rather than a frozen Python API. See [SDK compatibility policy](python-sdk-compatibility.md).

## Stream typed events

```python
from metis import Agent

agent = Agent(
    backend="openai",
    base_url="https://api.example.com/v1",
    model="your-model",
    permission_mode="ask",
    allowed_tools=["read_file", "search_files"],
)

stream = agent.run("Inspect this repository", workspace=".")
for event in stream:
    if event.kind in {"content_delta", "content"}:
        print(event.text, end="", flush=True)
    elif event.kind == "tool_call":
        print(f"\nTool: {event.tool}")
```

`AgentEvent` is an immutable `Mapping[str, Any]`. `event.as_dict()` returns a defensive copy suitable for JSON serialization. Known event kinds and the envelope are the same `metis.agent_event.v1` contract used by CLI JSONL and desktop SSE.

## Run to completion

```python
from metis import Agent, AgentEvent

def approve(event: AgentEvent) -> bool:
    # Replace with application policy or a real user confirmation.
    return event.tool == "read_file"

result = Agent(permission_mode="ask").run_to_completion(
    "Summarize the project",
    workspace=".",
    permission_handler=approve,
    on_event=lambda event: print(event.kind),
)

print(result.session_id, result.final_text, result.usage)
```

`run_to_completion` raises `AgentRunError` for an unsuccessful run by default. Pass `raise_on_error=False` to receive the non-zero `AgentResult` directly.

## Permission decisions

Permission requests are denied unless the caller explicitly approves them. There are two supported mechanisms:

1. Pass `permission_handler` to `run` or `run_to_completion`.
2. Drive the generator manually and call `stream.send(True)` or `stream.send(False)` immediately after a `permission_request` event.

The decision applies once. SDK defaults are `permission_mode="ask"`, desktop tools disabled, and MCP disabled; each can be explicitly changed on `Agent`.

## Configuration and credentials

Explicit `Agent` provider, endpoint, model, and API key values take precedence for that run. When omitted, the SDK uses the same environment, workspace/user settings, and Windows Credential Manager target (`Metis/LLM/API-Key`) as CLI and desktop.

Embedded runs temporarily apply runtime environment variables because the shared provider stack currently consumes that boundary. SDK runs are serialized, and the previous environment and working directory are restored when the generator finishes or is closed. Call `stream.close()` if a consumer stops before exhausting the event stream.

## Sessions

Every run uses the shared durable session store. `AgentResult.session_id` can be passed back as `session_id` to continue the same transcript and workspace:

```python
first = Agent().run_to_completion("Inspect the failing tests", workspace="D:/repo")
second = Agent().run_to_completion("Now fix the first failure", session_id=first.session_id)
```

Use `continue_session=True` instead of `session_id` to resume the most recently updated active session. The two options are mutually exclusive.
