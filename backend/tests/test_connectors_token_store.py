from __future__ import annotations

import pytest

from backend.runtime.connectors import token_store
from backend.runtime.mcp_client import MCPServerConfig, MCPManager, _stdio_env_for_config


@pytest.fixture(autouse=True)
def _clean_connector_env(monkeypatch):
    # Ensure no real machine env bleeds into these tests.
    for var in (
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "SLACK_BOT_TOKEN",
        "NOTION_TOKEN",
        "DATABASE_URL",
        "CLIENT_ID",
        "CLIENT_SECRET",
        "REDIRECT_URI",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_get_token_reads_injected_env(monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_injected")
    assert token_store.get_token("github") == "ghp_injected"
    assert token_store.is_connected("github") is True


def test_get_token_blank_or_missing_is_none(monkeypatch):
    assert token_store.get_token("github") is None
    monkeypatch.setenv("SLACK_BOT_TOKEN", "   ")
    assert token_store.get_token("slack") is None
    assert token_store.is_connected("slack") is False


def test_no_token_connector_is_never_connected():
    # filesystem has an empty token_env -> not a token-store concern.
    assert token_store.get_token("filesystem") is None
    assert token_store.is_connected("filesystem") is False


def test_credentials_file_readiness(monkeypatch, tmp_path):
    # google_drive needs both key-file env vars pointing to existing files.
    assert token_store.get_token("google_drive") is None  # no bearer token
    monkeypatch.delenv("GDRIVE_OAUTH_PATH", raising=False)
    monkeypatch.delenv("GDRIVE_CREDENTIALS_PATH", raising=False)
    assert token_store.credentials_ready("google_drive") is False
    assert token_store.is_connected("google_drive") is False

    keys = tmp_path / "keys.json"
    keys.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_OAUTH_PATH", str(keys))
    # only one of the two set -> still not ready
    assert token_store.credentials_ready("google_drive") is False

    cache = tmp_path / "cache.json"
    cache.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_CREDENTIALS_PATH", str(cache))
    assert token_store.credentials_ready("google_drive") is True
    assert token_store.is_connected("google_drive") is True


def test_credentials_file_missing_path_not_ready(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CREDENTIALS", "C:/no/such/file-xyz.json")
    assert token_store.credentials_ready("google_calendar") is False


def test_env_secret_readiness(monkeypatch):
    assert token_store.env_secrets_ready("x_api") is False
    assert token_store.is_connected("x_api") is False
    monkeypatch.setenv("CLIENT_ID", "x-client-id")
    assert token_store.env_secrets_ready("x_api") is False
    monkeypatch.setenv("CLIENT_SECRET", "x-client-secret")
    assert token_store.env_secrets_ready("x_api") is True
    assert token_store.is_connected("x_api") is True


def test_unknown_connector_is_none():
    assert token_store.get_token("does-not-exist") is None
    assert token_store.is_connected("does-not-exist") is False


def test_list_connected_only_lists_present_tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_x")
    monkeypatch.setenv("NOTION_TOKEN", "ntn_y")
    connected = set(token_store.list_connected())
    assert "github" in connected
    assert "notion" in connected
    monkeypatch.setenv("CLIENT_ID", "x-client-id")
    monkeypatch.setenv("CLIENT_SECRET", "x-client-secret")
    connected = set(token_store.list_connected())
    assert "x_api" in connected
    assert "slack" not in connected
    assert "filesystem" not in connected  # no-token connector


def test_mcp_env_resolves_token_from_store_by_service_id(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-store")
    config = MCPServerConfig(
        name="slack",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        token_env="SLACK_BOT_TOKEN",
        service_id="slack",
    )
    env = _stdio_env_for_config(config)
    assert env["SLACK_BOT_TOKEN"] == "xoxb-from-store"
    # red line: token never travels via args
    assert "xoxb-from-store" not in " ".join(config.args)
    # status must not leak the token
    manager = MCPManager()
    manager._all_configs = [config]
    status = manager.get_status()["slack"]
    assert "xoxb-from-store" not in str(status)


def test_explicit_auth_token_used_when_no_service_id(monkeypatch):
    # Backward compat: old config-file style with auth_token still injects.
    config = MCPServerConfig(
        name="legacy",
        command="some-mcp",
        args=["stdio"],
        auth_token="explicit-secret",
        token_env="LEGACY_TOKEN",
    )
    env = _stdio_env_for_config(config)
    assert env["LEGACY_TOKEN"] == "explicit-secret"
    assert "explicit-secret" not in config.args


def test_store_token_wins_over_explicit_auth_token(monkeypatch):
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_from_store")
    config = MCPServerConfig(
        name="github",
        command="github-mcp-server",
        args=["stdio"],
        auth_token="ghp_stale_in_config",
        token_env="GITHUB_PERSONAL_ACCESS_TOKEN",
        service_id="github",
    )
    env = _stdio_env_for_config(config)
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_from_store"
