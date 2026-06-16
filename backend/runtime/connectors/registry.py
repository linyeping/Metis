from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ConnectorDefinition:
    service_id: str
    display_name: str
    scopes: List[str]
    token_env: str
    mcp: Dict[str, Any]
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_CONNECTORS: Dict[str, ConnectorDefinition] = {
    "github": ConnectorDefinition(
        service_id="github",
        display_name="GitHub",
        scopes=["repo", "read:org"],
        token_env="GITHUB_PERSONAL_ACCESS_TOKEN",
        mcp={
            "command": "github-mcp-server",
            "args": ["stdio"],
            "example": {
                "mcpServers": {
                    "github": {
                        "command": "github-mcp-server",
                        "args": ["stdio"],
                        "token_env": "GITHUB_PERSONAL_ACCESS_TOKEN",
                    }
                }
            },
        },
        notes=[
            "GitHub Device Flow is preferred for desktop OAuth.",
            "A personal access token can be stored locally as a fallback.",
            "Tokens must be injected via env, never command arguments.",
        ],
    ),
    "gmail": ConnectorDefinition(
        service_id="gmail",
        display_name="Gmail",
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.labels",
        ],
        token_env="GOOGLE_OAUTH_ACCESS_TOKEN",
        mcp={
            "command": "npx",
            "args": ["@gongrzhe/server-gmail-autoauth-mcp"],
            "example": {
                "mcpServers": {
                    "gmail": {
                        "command": "npx",
                        "args": ["@gongrzhe/server-gmail-autoauth-mcp"],
                        "token_env": "GOOGLE_OAUTH_ACCESS_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Gmail uses PKCE loopback with the system browser.",
            "Sensitive Gmail scopes need Google OAuth verification for public distribution.",
            "First-party testing can run in Google OAuth test mode with explicit test users.",
        ],
    ),
}


def connector_catalog() -> List[Dict[str, Any]]:
    return [connector.to_dict() for connector in _CONNECTORS.values()]


def get_connector(service_id: str) -> Optional[ConnectorDefinition]:
    return _CONNECTORS.get(str(service_id or "").strip().lower())
