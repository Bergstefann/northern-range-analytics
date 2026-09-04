# Sharp Edges Analysis — eu-port-analytics

Full Surface Identification → Edge Case Probing → Threat Modeling → Validate Findings
workflow run against `config.py`, `cli.py`, `load/connection.py`, `load/upsert.py`,
`load/loader.py`, `load/schema.sql`, `ingest/eurostat_client.py`, `ingest/landing.py`,
`ingest/pull.py`, and `models.py`.

## Findings

### Medium — Configuration Cliff / Stringly-Typed Security
`src/port_analytics/load/connection.py:42-51`

`build_connection_string()` interpolates `SERVER`/`DATABASE`/`USERNAME`/`PASSWORD` env
vars into an ODBC connection string via an unescaped f-string. A credential containing
`;` can silently inject extra ODBC keys (e.g. downgrading `Encrypt`/
`TrustServerCertificate`) rather than erroring. No escaping test exists in
`tests/unit/test_connection.py`.

Recommendation: brace-quote and double internal `}` per ODBC rules, or reject control
characters in credentials.

### Low — Configuration Cliff
`src/port_analytics/load/connection.py:28`

`load_dotenv()` is called with no explicit path, so it searches upward from CWD for any
`.env`, risking a silent load of an unrelated file's credentials if run from an
unexpected directory.

## Checked, no findings

- SQL construction in `load/upsert.py` is fully parameterized (`?` placeholders, no
  string-built SQL with data).
- `connect()` fails loudly on missing/empty env vars.
- Secrets are never hardcoded; `.env` is gitignored.
- TLS settings are hardcoded, not configurable/downgradable via the application.
- `cli.py` exposes no bypass/force/skip-validation flags.

## Note on provenance

This report was written by the orchestrating session from the `sharp-edges-analyzer`
subagent's returned findings text — the subagent's own operating constraints prevent it
from writing report files directly, so it returned findings in its final message instead.
