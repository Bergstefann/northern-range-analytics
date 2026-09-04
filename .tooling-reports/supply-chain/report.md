# Supply Chain Risk Report — `eu-port-analytics`

**Scanned:** `.`  
**Commit:** `b2f3f274748b`  
**Manifests read:** `pyproject.toml`  
**Scanned at:** 2026-08-29T10:22:25+00:00  
**Direct dependencies:** 10 (PyPI 10)

## Summary

- **No known advisory affects any of the 10 direct dependencies** — but 10 of them were not checked at a project-resolved version (latest-release fallback or go.mod minimum; see Method and caveats).
- Transitive dependencies were **not examined**: no lockfile resolves the transitive tree (package-lock.json, uv.lock, or a go 1.17+ go.mod).
- 0 of 10 dependencies carry at least one finding, 0 of which reach production.
- Weakest coverage: **Install-time script execution**, established for 0 of 10; the Coverage section lists every criterion.

## Production dependencies

5 dependencies are declared as runtime dependencies and ship in the built artifact. Advisory status is given for every one, clean or not.

| Dependency | Version | Advisories | Other findings |
|---|---|---|---|
| `pydantic` | 2.13.5 (latest, not the project's pin) | none known | — |
| `pyodbc` | 5.3.0 (latest, not the project's pin) | none known | — |
| `python-dotenv` | 1.2.3 (latest, not the project's pin) | none known | — |
| `requests` | 2.34.2 (latest, not the project's pin) | none known | — |
| `typer` | 0.27.2 (latest, not the project's pin) | none known | — |

## Findings

No dependency was flagged on these criteria.

## Upstream repository and CI hygiene — OpenSSF Scorecard

These criteria describe each dependency's own repository, not the audited
project. Remediation, where any exists, is upstream.

No criterion in this tier was assessable for any dependency.

## Transitive advisories

Not examined: no lockfile resolves the transitive tree (package-lock.json, uv.lock, or a go 1.17+ go.mod). Commit a lockfile to close this gap.

## Informational

Measured, not flagged.

- **Publish provenance**: not determinable for any dependency in this project.
- **Security policy published**: 9 of 10 publish a security policy.
  Without: `pyodbc`
- **Download volume**: not determinable for any dependency in this project.

## Coverage

What was and was not measured, per criterion.

| Criterion | Tier | Assessed | Flagged | Not assessable |
|---|---|---|---|---|
| Known advisories | A | 10/10 | 0 | 0 |
| Deprecated or yanked | A | 10/10 | 0 | 0 |
| Repository archived | A | 10/10 | 0 | 0 |
| Maintenance activity | A | 10/10 | 0 | 0 |
| Publisher concentration | B | 0/10 | 0 | 10 |
| Install-time script execution | B | 0/10 | 0 | 10 |
| Dangerous CI workflow | scorecard | 0/10 | 0 | 10 |
| CI token permissions | scorecard | 0/10 | 0 | 10 |
| Checked-in binaries | scorecard | 0/10 | 0 | 10 |
| Changes reviewed by a second person | scorecard | 0/10 | 0 | 10 |
| Publish provenance | info | 0/10 | 0 | 10 |
| Security policy published | info | 10/10 | 0 | 0 |
| Download volume | info | 0/10 | 0 | 10 |

## Not assessable

**Checked-in binaries**

- 10 dependencies — the Scorecard API did not answer: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Changes reviewed by a second person**

- 10 dependencies — the Scorecard API did not answer: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Dangerous CI workflow**

- 10 dependencies — the Scorecard API did not answer: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Download volume**

- 10 dependencies — PyPI's download counters are disabled and return -1: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Install-time script execution**

- 10 dependencies — install-time execution depends on whether a wheel or an sdist is installed, which this collector does not determine: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Publish provenance**

- 10 dependencies — PyPI publish attestations are not read by this collector: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**Publisher concentration**

- 10 dependencies — PyPI publishes no upload ACL, so who can publish this package is not observable: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

**CI token permissions**

- 10 dependencies — the Scorecard API did not answer: `mypy`, `pydantic`, `pyodbc`, `pytest`, `pytest-cov`, `python-dotenv` and 4 more

## Method and caveats

- cache directory ownership was not verified: os.getuid is POSIX-only, so a cache at C:\Users\thoma\AppData\Local\Temp\supply-chain-risk-auditor-cache owned by another user would not be detected on this platform. Keep the cache under your own user profile, or pass --cache with a private path.
- 10 dependencies are specified as a version range with no lockfile, so their advisories were matched against the current latest release rather than against what this project installs — commit a lockfile for an exact answer: mypy, pydantic, pyodbc, pytest, pytest-cov, python-dotenv, requests, ruff ...
- Optional tooling detected: npm. Not installed, so not used: bundler-audit, cargo-audit, osv-scanner, pip-audit.
- Direct dependencies only. Advisories attach to the package that ships the affected code, so an umbrella package can look clean while its components are not — rails 5.0.0 reports 0 advisories where actionpack 5.0.0 reports 10. Transitive dependencies were not examined: no lockfile resolves the transitive tree (package-lock.json, uv.lock, or a go 1.17+ go.mod).
- HTTP sources: 31 fetched, 10 served from cache (oldest 0.0h old), 0 refetched as stale, 0 unavailable offline, 10 errors.
