from __future__ import annotations

from backend.runtime.connectors import connector_catalog, get_connector
from backend.runtime.mcp_client import MCPServerConfig, MCPManager, _decode_jsonrpc_http_response, _stdio_env_for_config


def test_connector_registry_includes_github_and_gmail() -> None:
    services = {item["service_id"] for item in connector_catalog()}
    assert {"github", "gmail"} <= services
    assert get_connector("github").token_env == "GITHUB_PERSONAL_ACCESS_TOKEN"  # type: ignore[union-attr]
    assert get_connector("gmail").token_env == "GOOGLE_OAUTH_ACCESS_TOKEN"  # type: ignore[union-attr]


def test_mcp_auth_token_is_env_only_and_not_status_or_args() -> None:
    config = MCPServerConfig(
        name="github",
        command="github-mcp-server",
        args=["stdio"],
        auth_token="secret-token",
        token_env="GITHUB_PERSONAL_ACCESS_TOKEN",
    )
    env = _stdio_env_for_config(config)
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "secret-token"
    assert "secret-token" not in config.args

    manager = MCPManager()
    manager._all_configs = [config]
    status = manager.get_status()["github"]
    assert status["config"]["args"] == ["stdio"]
    assert "secret-token" not in str(status)


def test_connector_catalog_expanded_set() -> None:
    services = {item["service_id"] for item in connector_catalog()}
    expected = {
        "github",
        "gmail",
        "slack",
        "notion",
        "filesystem",
        "postgres",
        "x_docs",
        "x_api",
        # Modeled as credentials_file connectors (T4), not bearer-token.
        "google_drive",
        "google_calendar",
    }
    assert expected <= services


def test_google_file_connectors_use_credentials_file_model() -> None:
    from backend.runtime.connectors.registry import AUTH_CREDENTIALS_FILE

    for service_id, expected_envs in (
        ("google_drive", {"GDRIVE_OAUTH_PATH", "GDRIVE_CREDENTIALS_PATH"}),
        ("google_calendar", {"GOOGLE_OAUTH_CREDENTIALS"}),
    ):
        conn = get_connector(service_id)
        assert conn is not None
        assert conn.auth_kind == AUTH_CREDENTIALS_FILE
        assert conn.token_env == ""  # no bearer token
        assert set(conn.credentials_envs) == expected_envs


def test_filesystem_connector_has_no_token() -> None:
    fs = get_connector("filesystem")
    assert fs is not None
    assert fs.token_env == ""
    assert fs.scopes == []


def test_postgres_connector_uses_database_url_env_not_args() -> None:
    pg = get_connector("postgres")
    assert pg is not None
    assert pg.token_env == "DATABASE_URL"
    joined_args = " ".join(str(a) for a in pg.mcp.get("args", [])).lower()
    assert "password" not in joined_args
    assert "postgresql://" not in joined_args


def test_x_connectors_are_modeled_as_official_mcp_entrypoints() -> None:
    from backend.runtime.connectors.registry import AUTH_ENV_SECRETS, AUTH_NONE

    docs = get_connector("x_docs")
    assert docs is not None
    assert docs.auth_kind == AUTH_NONE
    assert docs.mcp.get("url") == "https://docs.x.com/mcp"

    api = get_connector("x_api")
    assert api is not None
    assert api.auth_kind == AUTH_ENV_SECRETS
    assert set(api.secret_envs) == {"CLIENT_ID", "CLIENT_SECRET"}
    assert "CLIENT_SECRET" not in " ".join(str(arg) for arg in api.mcp.get("args", []))
    assert api.mcp.get("args") == ["-y", "@xdevplatform/xurl", "mcp", "https://api.x.com/mcp"]


def test_streamable_http_sse_jsonrpc_response_is_decoded() -> None:
    class Response:
        headers = {"content-type": "text/event-stream"}
        text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

        def json(self):  # pragma: no cover - should not be called for SSE
            raise AssertionError("SSE response should be decoded from data frames")

    decoded = _decode_jsonrpc_http_response(Response())
    assert decoded["result"]["ok"] is True


def test_no_connector_leaks_secret_in_args() -> None:
    # 红线：凭据绝不进 args。args 里不得出现凭据样式的字面值。
    for item in connector_catalog():
        args = item["mcp"].get("args", [])
        joined = " ".join(str(a) for a in args).lower()
        for marker in ("xoxb-", "ntn_", "secret_", "postgresql://", "password"):
            assert marker not in joined, f"{item['service_id']} args 疑似含凭据: {args}"
