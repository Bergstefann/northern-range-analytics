"""Builds the Azure SQL connection from environment variables. Never
holds a connection string or credential as a literal -- those come from
.env (gitignored) or the real process environment, per the spec's
standing rule that secrets never land in git."""

from __future__ import annotations

import os

import pyodbc
from dotenv import load_dotenv

ODBC_DRIVER = "{ODBC Driver 18 for SQL Server}"

REQUIRED_ENV_VARS = (
    "NORTHERN_RANGE_SQL_SERVER",
    "NORTHERN_RANGE_SQL_DATABASE",
    "NORTHERN_RANGE_SQL_USERNAME",
    "NORTHERN_RANGE_SQL_PASSWORD",
)


class MissingConnectionConfig(RuntimeError):
    pass


def build_connection_string() -> str:
    load_dotenv()
    values = {name: os.environ.get(name) for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MissingConnectionConfig(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them in .env (see .env.example) or the process environment."
        )

    server = values["NORTHERN_RANGE_SQL_SERVER"]
    database = values["NORTHERN_RANGE_SQL_DATABASE"]
    username = values["NORTHERN_RANGE_SQL_USERNAME"]
    password = values["NORTHERN_RANGE_SQL_PASSWORD"]

    return (
        f"DRIVER={ODBC_DRIVER};"
        f"SERVER=tcp:{server},1433;"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )


def connect() -> pyodbc.Connection:
    return pyodbc.connect(build_connection_string())
