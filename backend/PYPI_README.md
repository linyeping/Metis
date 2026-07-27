# Metis Agent SDK

`metis-agent-sdk` embeds the same typed agent runtime used by Metis Desktop and the Metis CLI. It exposes streaming events, tool permission decisions, durable sessions, model routing, and the `metis.agent_event.v1` contract without starting a subprocess.

```bash
python -m pip install metis-agent-sdk
```

```python
from metis import Agent

result = Agent(
    backend="openai-compatible",
    base_url="https://api.example.com/v1",
    model="your-model",
).run_to_completion("Summarize this repository", workspace=".")

print(result.final_text)
```

The distribution name is `metis-agent-sdk`; the import package is `metis`. Public SDK API compatibility is tracked separately through `metis.SDK_API_VERSION`.

See the [full SDK guide](https://github.com/linyeping/Metis/blob/main/docs/python-sdk.md) for event streaming, permission handling, sessions, and configuration.
