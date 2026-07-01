from __future__ import annotations

import os
import shutil

import pytest

from backend.runtime.connectors import manager


@pytest.fixture(autouse=True)
def _clean_connector_env(monkeypatch):
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


# --- build_config (pure, no spawn) ---

def test_build_config_substitutes_allowed_dir():
    config = manager.build_config("filesystem", allowed_dir="D:/work/space")
    assert config.command == "npx"
    assert "D:/work/space" in config.args
    assert manager.ALLOWED_DIR_PLACEHOLDER not in config.args
    assert config.service_id == "filesystem"
    assert config.token_env == ""


def test_build_config_sets_service_id_and_token_env():
    config = manager.build_config("slack")
    assert config.service_id == "slack"
    assert config.token_env == "SLACK_BOT_TOKEN"
    # no allowed_dir substitution for non-filesystem connectors
    assert manager.ALLOWED_DIR_PLACEHOLDER not in config.args


def test_build_config_token_is_in_memory_only():
    config = manager.build_config("slack", token="xoxb-session")
    assert config.auth_token == "xoxb-session"


def test_build_config_unknown_raises():
    with pytest.raises(ValueError):
        manager.build_config("does-not-exist")


def test_build_config_forwards_credentials_file_paths(monkeypatch, tmp_path):
    keys = tmp_path / "gcp-oauth.keys.json"
    keys.write_text("{}", encoding="utf-8")
    cache = tmp_path / "creds.json"
    cache.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_OAUTH_PATH", str(keys))
    monkeypatch.setenv("GDRIVE_CREDENTIALS_PATH", str(cache))
    config = manager.build_config("google_drive")
    assert config.env["GDRIVE_OAUTH_PATH"] == str(keys)
    assert config.env["GDRIVE_CREDENTIALS_PATH"] == str(cache)
    assert config.token_env == ""  # no bearer token for credentials_file


def test_build_config_x_docs_preserves_remote_url():
    config = manager.build_config("x_docs")
    assert config.command is None
    assert config.url == "https://docs.x.com/mcp"
    assert config.args == []
    assert config.token_env == ""


def test_build_config_x_api_forwards_env_secrets(monkeypatch):
    monkeypatch.setenv("CLIENT_ID", "x-client-id")
    monkeypatch.setenv("CLIENT_SECRET", "x-client-secret")
    config = manager.build_config("x_api")
    assert config.command == "npx"
    assert config.args == ["-y", "@xdevplatform/xurl", "mcp", "https://api.x.com/mcp"]
    assert config.env["CLIENT_ID"] == "x-client-id"
    assert config.env["CLIENT_SECRET"] == "x-client-secret"
    assert config.env["REDIRECT_URI"] == "http://localhost:8080/callback"
    assert config.token_env == ""
    assert "x-client-secret" not in " ".join(config.args)


def test_connect_credentials_file_without_keys_is_rejected(monkeypatch):
    monkeypatch.delenv("GDRIVE_OAUTH_PATH", raising=False)
    monkeypatch.delenv("GDRIVE_CREDENTIALS_PATH", raising=False)
    result = manager.connect("google_drive")
    assert result["ok"] is False
    assert "one-time auth" in result["error"] or "keys file" in result["error"]


def test_connect_env_secret_connector_without_config_is_rejected(monkeypatch):
    monkeypatch.delenv("CLIENT_ID", raising=False)
    monkeypatch.delenv("CLIENT_SECRET", raising=False)
    result = manager.connect("x_api")
    assert result["ok"] is False
    assert "CLIENT_ID" in result["error"]


# --- connect rejection paths (no spawn) ---

def test_connect_unknown_connector_errors():
    result = manager.connect("does-not-exist")
    assert result["ok"] is False
    assert "unknown connector" in result["error"]


def test_connect_token_connector_without_token_is_rejected():
    # SLACK_BOT_TOKEN cleared by fixture, no token passed -> rejected before spawn.
    result = manager.connect("slack")
    assert result["ok"] is False
    assert "no token" in result["error"]


def test_connect_filesystem_without_allowed_dir_is_rejected():
    result = manager.connect("filesystem")
    assert result["ok"] is False
    assert "allowed_dir" in result["error"]


# --- list / disconnect ---

def test_list_connectors_exposes_state_fields():
    items = {item["service_id"]: item for item in manager.list_connectors()}
    assert "filesystem" in items
    fs = items["filesystem"]
    for key in ("has_token", "active", "tools_count", "last_error"):
        assert key in fs
    assert fs["has_token"] is False  # no-token connector
    assert fs["active"] is False  # nothing connected in a fresh process


def test_disconnect_not_connected_errors():
    result = manager.disconnect("slack")
    assert result["ok"] is False
    # Either "no active MCP manager" (fresh process) or "was not connected".
    assert "not connected" in result["error"] or "no active MCP manager" in result["error"]


def test_test_not_connected_errors():
    result = manager.test("slack")
    assert result["ok"] is False
    assert "not connected" in result["error"]


# --- real e2e (spawns the filesystem MCP server via npx) ---
# Gated behind METIS_E2E_MCP=1 so the default suite stays hermetic/fast; the
# first run downloads the npm package. No token needed (filesystem connector).
@pytest.mark.skipif(
    os.environ.get("METIS_E2E_MCP") != "1" or not shutil.which("npx"),
    reason="set METIS_E2E_MCP=1 and have npx on PATH to run the live MCP e2e",
)
def test_e2e_filesystem_connect_test_disconnect(tmp_path):
    from backend.runtime.tool_registry import ToolRegistry

    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    reg = ToolRegistry()

    def fs_tools():
        return [name for name in reg._tools if name.startswith("mcp_filesystem_")]

    connect_result = manager.connect("filesystem", allowed_dir=str(tmp_path), registry=reg)
    assert connect_result["ok"], connect_result
    assert fs_tools(), "filesystem tools should be registered after connect"

    test_result = manager.test("filesystem")
    assert test_result["ok"], test_result

    disconnect_result = manager.disconnect("filesystem", registry=reg)
    assert disconnect_result["ok"], disconnect_result
    assert fs_tools() == [], "filesystem tools should be gone after disconnect"
