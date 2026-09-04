# Security Audit Findings — eu-port-analytics

Phase 1 (read-only analysis), 2026-08-29. Composition: audit-context-builder →
insecure-defaults → supply-chain-risk-auditor → sharp-edges → fp-check.

## Verified findings

Both findings below were run through fp-check's Standard Verification (data flow, 
exploitability, impact, PoC sketch, devil's-advocate, six-gate review). Both are real 
code defects; neither passes fp-check's Gate 2 (Reachability) or Gate 3 (Real Impact) 
as an attacker-exploitable vulnerability, because in both cases the precondition for 
exploitation is that the "attacker" already holds equivalent or greater access than 
what the defect would grant (operator's own DB secret store, or local filesystem write 
access near the invocation path). They remain valid code-quality / misuse-resistance 
findings, downgraded from the sharp-edges pass's initial framing.

### Code defect (not an attacker-exploitable vulnerability) — ODBC connection-string injection via unescaped credential interpolation
`src/port_analytics/load/connection.py:42-51` (sharp-edges; fp-check gate review below)

`build_connection_string()` splices `SERVER`/`DATABASE`/`USERNAME`/`PASSWORD` env vars
into a `;`-delimited ODBC connection string via an f-string, with no brace-quoting. A
credential containing `;` injects additional ODBC keys (e.g. downgrading
`Encrypt`/`TrustServerCertificate`) instead of erroring. `.env.example` places no
character restrictions on `NORTHERN_RANGE_SQL_PASSWORD`. No escaping test exists in
`tests/unit/test_connection.py`. Confirmed via `grep` that these env vars are read in
exactly one place and never derived from CLI args, network responses, or ingested data
— the only source is the operator's own `.env`/process environment.

fp-check gate review: **Gate 2 (Reachability) FAIL** — not attacker-controlled; the
value is exclusively deployer-supplied secret material. **Gate 3 (Real Impact) FAIL** —
whoever can set the value already possesses the legitimate DB credential, so no
privilege is gained beyond what setting the password already grants. **Verdict: FALSE
POSITIVE as a vulnerability; valid as a code-quality defect** (fragile parsing that
could silently downgrade TLS validation if an operator's password happens to contain
`;`).

Recommendation: brace-quote each interpolated value per ODBC rules (doubling internal
`}`), or reject control/delimiter characters in credentials before building the string.
Worth fixing for robustness, not urgent as a security response.

### Code defect (not an attacker-exploitable vulnerability) — `load_dotenv()` called with no explicit path
`src/port_analytics/load/connection.py:28` (sharp-edges; fp-check gate review below)

`load_dotenv()` is called with `dotenv_path=None`, which defaults to `python-dotenv`'s
upward directory search from CWD, not a path scoped to the project. Running the CLI
from an unexpected working directory could silently load an unrelated `.env`. Confirmed
via `inspect.signature(dotenv.load_dotenv)`.

fp-check gate review: **Gate 2 (Reachability) FAIL** — requires an attacker to already
hold local filesystem write access to an ancestor directory of the invocation path; no
such attacker-reachable path is evidenced in this codebase's deployment shape (no
multi-tenant/shared-CWD scenario). Impact (credential/data exfiltration via a planted
`.env`) is plausible but entirely gated behind that precondition. **Verdict: FALSE
POSITIVE as a vulnerability; valid as a low-severity operational-hygiene defect.**

Recommendation: pass an explicit path, e.g. `load_dotenv(Path(__file__).parents[3] / ".env")`.

## Coverage gaps

- `src/port_analytics/models.py` has no audit-context function-analysis note (11 of 12
  source modules covered; the audit-context pass predates this file or it was missed).
  Not separately re-verified in this pass.

## Skipped / not run

- **CodeRabbit** — NOT INSTALLED. See `.tooling-reports/coderabbit.md`.
- **karpathy skill** — not applicable (behavioral guidance, not an audit skill that
  produces findings). See `.tooling-reports/karpathy.md`.
- **insecure-defaults** — NOT RUN. Required `Workflow` tool unavailable in this session.
  See `.tooling-reports/insecure-defaults.md`.

## Clean

- **supply-chain-risk-auditor**: 10/10 direct PyPI dependencies, no known advisories.
  No lockfile present, so versions were checked against latest release, not the
  project's resolved pins — see `.tooling-reports/supply-chain/report.md`. OpenSSF
  Scorecard / CI-hygiene tier unassessable (API did not answer) for all 10.
- **sharp-edges**, other surfaces checked with no findings: SQL in `load/upsert.py` is
  fully parameterized; `connect()` fails loudly on missing/empty env vars; no hardcoded
  secrets; `.env` gitignored; TLS hardcoded (not downgradable via app config); `cli.py`
  exposes no bypass/skip-validation flags.
