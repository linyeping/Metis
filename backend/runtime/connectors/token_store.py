"""Read-only view over connector tokens that the desktop layer injected.

Architecture (important — do not "fix" this by adding encryption here):

The Electron desktop layer owns connector secret storage. It encrypts each
connector token with OS-level ``safeStorage`` (DPAPI on Windows, Keychain on
macOS, libsecret on Linux) into ``<userData>/connectors/<service>.enc``, and at
backend-spawn time it decrypts every stored token and injects it into THIS
process's environment under the connector's ``token_env`` — exactly mirroring how
the LLM key becomes ``METIS_LLM_API_KEY`` (see desktop/electron/backend.cjs).

The Python backend can NOT decrypt ``safeStorage`` blobs (they are bound to the
OS user via DPAPI/Keychain and only Electron's ``os_crypt`` can open them). So
this module is a *read-only* view over the injected env vars, keyed by the
connector registry. Writing or clearing tokens is the desktop's job
(desktop/electron/oauth.cjs); the backend must never self-encrypt or persist
plaintext secrets.

No-token connectors (e.g. ``filesystem``, whose ``token_env`` is empty) are not
this module's concern: they carry no credential, so ``get_token`` returns None,
``is_connected`` returns False, and they never appear in ``list_connected``.
Their readiness (e.g. an allowed directory) is decided by the connector manager,
not here.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .registry import AUTH_CREDENTIALS_FILE, AUTH_ENV_SECRETS, connector_catalog, get_connector


def _token_env_for(service_id: str) -> str:
    connector = get_connector(service_id)
    if connector is None:
        return ""
    return str(connector.token_env or "").strip()


def get_token(service_id: str) -> Optional[str]:
    """Return a bearer connector's token (from the desktop-injected env), or None.

    None when the connector is unknown, isn't a bearer-token connector (no
    ``token_env``), or the env var is unset/blank. credentials_file connectors
    have no bearer token — use ``credentials_ready`` for their readiness.
    """
    token_env = _token_env_for(service_id)
    if not token_env:
        return None
    value = os.environ.get(token_env, "").strip()
    return value or None


def credentials_ready(service_id: str) -> bool:
    """For credentials_file connectors: every credentials_env points to an existing file."""
    connector = get_connector(service_id)
    if connector is None or connector.auth_kind != AUTH_CREDENTIALS_FILE:
        return False
    envs = list(connector.credentials_envs or [])
    if not envs:
        return False
    for env_name in envs:
        path = os.environ.get(env_name, "").strip()
        if not path or not os.path.isfile(path):
            return False
    return True


def env_secrets_ready(service_id: str) -> bool:
    """For env_secrets connectors: every required secret env var is present."""
    connector = get_connector(service_id)
    if connector is None or connector.auth_kind != AUTH_ENV_SECRETS:
        return False
    envs = list(connector.secret_envs or [])
    if not envs:
        return False
    return all(bool(os.environ.get(env_name, "").strip()) for env_name in envs)


def is_connected(service_id: str) -> bool:
    """True when a connector has the credential it needs available.

    bearer_token -> token present in env; credentials_file -> all keys files
    exist; none -> False (no credential; readiness decided by the manager).
    """
    connector = get_connector(service_id)
    if connector is None:
        return False
    if connector.auth_kind == AUTH_CREDENTIALS_FILE:
        return credentials_ready(service_id)
    if connector.auth_kind == AUTH_ENV_SECRETS:
        return env_secrets_ready(service_id)
    return get_token(service_id) is not None


def list_connected() -> List[str]:
    """Service ids of every connector whose credential is currently available."""
    return [
        item["service_id"]
        for item in connector_catalog()
        if is_connected(item["service_id"])
    ]
