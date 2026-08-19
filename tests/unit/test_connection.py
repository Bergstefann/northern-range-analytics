from __future__ import annotations

import pytest

from port_analytics.load.connection import (
    REQUIRED_ENV_VARS,
    MissingConnectionConfig,
    build_connection_string,
)


def test_build_connection_string_raises_loudly_when_env_vars_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # load_dotenv() searches upward from the caller's file location, not
    # cwd -- it would still find this project's real .env otherwise.
    monkeypatch.setattr("port_analytics.load.connection.load_dotenv", lambda: None)

    with pytest.raises(MissingConnectionConfig, match="NORTHERN_RANGE_SQL_SERVER"):
        build_connection_string()


def test_build_connection_string_succeeds_with_all_env_vars_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NORTHERN_RANGE_SQL_SERVER", "myserver.database.windows.net")
    monkeypatch.setenv("NORTHERN_RANGE_SQL_DATABASE", "mydb")
    monkeypatch.setenv("NORTHERN_RANGE_SQL_USERNAME", "admin")
    monkeypatch.setenv("NORTHERN_RANGE_SQL_PASSWORD", "secret")

    conn_str = build_connection_string()

    assert "SERVER=tcp:myserver.database.windows.net,1433" in conn_str
    assert "DATABASE=mydb" in conn_str
    assert "UID=admin" in conn_str
    assert "PWD=secret" in conn_str
    assert "Encrypt=yes" in conn_str
