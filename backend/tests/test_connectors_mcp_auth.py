from __future__ import annotations

from backend.runtime.connectors import connector_catalog, get_connector
from backend.runtime.mcp_client import MCPServerConfig, MCPManager, _stdio_env_for_config


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
