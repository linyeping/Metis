from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Connector auth kinds:
#   "bearer_token"     - a single secret injected via token_env (github/slack/...).
#   "credentials_file" - no bearer token; the server reads OAuth client keys from
#                        a file whose PATH is given via credentials_envs, and does
#                        its own one-time interactive browser auth, caching its own
#                        token on disk (Google Drive/Calendar). The paths are
#                        user-specific, so the registry only names the env vars.
#   "env_secrets"      - multiple secret env vars are stored by the desktop via
#                        safeStorage and forwarded to a stdio bridge at activation
#                        time (X API via the official xurl bridge).
#   "none"             - no credential at all (filesystem).
AUTH_BEARER_TOKEN = "bearer_token"
AUTH_CREDENTIALS_FILE = "credentials_file"
AUTH_ENV_SECRETS = "env_secrets"
AUTH_NONE = "none"


@dataclass(frozen=True)
class ConnectorDefinition:
    service_id: str
    display_name: str
    scopes: List[str]
    token_env: str
    mcp: Dict[str, Any]
    notes: List[str]
    auth_kind: str = AUTH_BEARER_TOKEN
    # For auth_kind == credentials_file: env var names that must point to existing
    # files for the connector to be usable (and that get forwarded to the server).
    credentials_envs: List[str] = field(default_factory=list)
    # For auth_kind == env_secrets: secret env vars that must be present before
    # activation, plus optional runtime env vars that are forwarded when set.
    secret_envs: List[str] = field(default_factory=list)
    optional_secret_envs: List[str] = field(default_factory=list)

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
    "x_docs": ConnectorDefinition(
        service_id="x_docs",
        display_name="X Docs",
        scopes=["docs.search", "docs.read"],
        token_env="",
        auth_kind=AUTH_NONE,
        mcp={
            "url": "https://docs.x.com/mcp",
            "example": {
                "mcpServers": {
                    "x_docs": {
                        "url": "https://docs.x.com/mcp",
                    }
                }
            },
        },
        notes=[
            "Official hosted X documentation MCP: https://docs.x.com/mcp.",
            "Read-only: search and retrieve public X docs; no user X auth required.",
            "Uses Streamable HTTP MCP responses (text/event-stream JSON-RPC).",
        ],
    ),
    "x_api": ConnectorDefinition(
        service_id="x_api",
        display_name="X API",
        scopes=["tweet.read", "users.read", "offline.access"],
        token_env="",
        auth_kind=AUTH_ENV_SECRETS,
        secret_envs=["CLIENT_ID", "CLIENT_SECRET"],
        optional_secret_envs=["REDIRECT_URI"],
        mcp={
            "command": "npx",
            "args": ["-y", "@xdevplatform/xurl", "mcp", "https://api.x.com/mcp"],
            "env": {"REDIRECT_URI": "http://localhost:8080/callback"},
            "example": {
                "mcpServers": {
                    "x_api": {
                        "command": "npx",
                        "args": ["-y", "@xdevplatform/xurl", "mcp", "https://api.x.com/mcp"],
                        "env": {
                            "CLIENT_ID": "<X Developer OAuth 2.0 client id>",
                            "CLIENT_SECRET": "<X Developer OAuth 2.0 client secret>",
                            "REDIRECT_URI": "http://localhost:8080/callback",
                        },
                    }
                }
            },
        },
        notes=[
            "Official X API MCP endpoint is https://api.x.com/mcp; Metis uses the official xurl stdio bridge.",
            "Create an X Developer app, enable OAuth 2.0, and register the redirect URI (default http://localhost:8080/callback).",
            "CLIENT_ID and CLIENT_SECRET are encrypted by Electron safeStorage, injected as env vars, and never passed in args.",
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
    "google_drive": ConnectorDefinition(
        service_id="google_drive",
        display_name="Google Drive",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        token_env="",
        auth_kind=AUTH_CREDENTIALS_FILE,
        credentials_envs=["GDRIVE_OAUTH_PATH", "GDRIVE_CREDENTIALS_PATH"],
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gdrive"],
            "example": {
                "mcpServers": {
                    "google_drive": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-gdrive"],
                        "env": {
                            "GDRIVE_OAUTH_PATH": "<path to gcp-oauth.keys.json>",
                            "GDRIVE_CREDENTIALS_PATH": "<path to cache .gdrive-server-credentials.json>",
                        },
                    }
                }
            },
        },
        notes=[
            "Credentials-file model (NOT a bearer token): GDRIVE_OAUTH_PATH points to "
            "your downloaded OAuth client keys json; GDRIVE_CREDENTIALS_PATH is where "
            "the server caches its own token after a one-time interactive auth.",
            "One-time auth is server-driven and opens the system browser "
            "(`npx -y @modelcontextprotocol/server-gdrive auth`) — it CANNOT be done "
            "headlessly by the backend; connect() only works after that cache exists.",
            "Verified on npmjs: @modelcontextprotocol/server-gdrive.",
        ],
    ),
    "google_calendar": ConnectorDefinition(
        service_id="google_calendar",
        display_name="Google Calendar",
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        token_env="",
        auth_kind=AUTH_CREDENTIALS_FILE,
        credentials_envs=["GOOGLE_OAUTH_CREDENTIALS"],
        mcp={
            "command": "npx",
            "args": ["-y", "@cocal/google-calendar-mcp"],
            "example": {
                "mcpServers": {
                    "google_calendar": {
                        "command": "npx",
                        "args": ["-y", "@cocal/google-calendar-mcp"],
                        "env": {"GOOGLE_OAUTH_CREDENTIALS": "<path to gcp-oauth.keys.json>"},
                    }
                }
            },
        },
        notes=[
            "Credentials-file model: GOOGLE_OAUTH_CREDENTIALS points to your downloaded "
            "OAuth client keys json (Desktop app type). The server caches its own token "
            "after a one-time interactive browser auth.",
            "Package @cocal/google-calendar-mcp — the @modelcontextprotocol/server-google-calendar "
            "name does NOT exist on npm (verified 2026-06).",
        ],
    ),
    "slack": ConnectorDefinition(
        service_id="slack",
        display_name="Slack",
        scopes=["channels:read", "channels:history", "chat:write"],
        token_env="SLACK_BOT_TOKEN",
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
            "example": {
                "mcpServers": {
                    "slack": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-slack"],
                        "token_env": "SLACK_BOT_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Bot token (xoxb-) via env; the server also reads SLACK_TEAM_ID (and "
            "optionally SLACK_CHANNEL_IDS) from env, never from args.",
            "chat:write is a send action; require explicit permission before posting.",
            "Verified on npmjs: @modelcontextprotocol/server-slack, bin mcp-server-slack.",
        ],
    ),
    "notion": ConnectorDefinition(
        service_id="notion",
        display_name="Notion",
        scopes=["read", "update", "insert"],
        token_env="NOTION_TOKEN",
        mcp={
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "example": {
                "mcpServers": {
                    "notion": {
                        "command": "npx",
                        "args": ["-y", "@notionhq/notion-mcp-server"],
                        "token_env": "NOTION_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Integration token from a Notion internal integration; pages must be "
            "shared with it before the server can see them.",
            "The real env var is NOTION_TOKEN (an earlier draft of this entry assumed "
            "NOTION_API_KEY, which does not exist for this package).",
            "Verified on npmjs: @notionhq/notion-mcp-server, bin notion-mcp-server.",
        ],
    ),
    "filesystem": ConnectorDefinition(
        service_id="filesystem",
        display_name="Local Filesystem",
        scopes=[],
        token_env="",
        auth_kind=AUTH_NONE,
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "<ALLOWED_DIR>"],
            "example": {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "<ALLOWED_DIR>"],
                    }
                }
            },
        },
        notes=[
            "No token. The directory arg is not a secret -- it scopes access and must "
            "stay inside the current workspace boundary.",
            "<ALLOWED_DIR> is a placeholder; the connector manager substitutes the "
            "actual allowed workspace path at connect time.",
            "Verified on npmjs: @modelcontextprotocol/server-filesystem, bin mcp-server-filesystem.",
        ],
    ),
    "postgres": ConnectorDefinition(
        service_id="postgres",
        display_name="PostgreSQL",
        scopes=[],
        token_env="DATABASE_URL",
        mcp={
            "command": "npx",
            "args": ["-y", "mcp-postgres@latest"],
            "example": {
                "mcpServers": {
                    "postgres": {
                        "command": "npx",
                        "args": ["-y", "mcp-postgres@latest"],
                        "token_env": "DATABASE_URL",
                    }
                }
            },
        },
        notes=[
            "Connection string (it contains the password) MUST come via the "
            "DATABASE_URL env var, never as an arg.",
            "Package is mcp-postgres (not under the @modelcontextprotocol scope); it "
            "also accepts split DB_HOST/DB_USER/... vars, but DATABASE_URL is the "
            "single-env-var form that fits this registry's token_env model.",
            "Verified on npmjs: mcp-postgres, bin mcp-postgres (server.mjs).",
        ],
    ),
}


def connector_catalog() -> List[Dict[str, Any]]:
    return [connector.to_dict() for connector in _CONNECTORS.values()]


def get_connector(service_id: str) -> Optional[ConnectorDefinition]:
    return _CONNECTORS.get(str(service_id or "").strip().lower())
