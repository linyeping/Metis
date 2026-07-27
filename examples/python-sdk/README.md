# Metis Agent SDK example

Install the SDK and configure a model endpoint:

```powershell
python -m pip install metis-agent-sdk
$env:METIS_LLM_BACKEND = "openai-compatible"
$env:METIS_LLM_BASE_URL = "https://api.example.com/v1"
$env:METIS_LLM_API_KEY = "..."
$env:METIS_LLM_MODEL = "your-model"
python stream_agent.py "Summarize this repository"
```

The example streams typed content and tool events, denies write/execute permission requests by default, and prints the durable session id on completion.
