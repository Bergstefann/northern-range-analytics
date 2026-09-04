# Function analyses: src/port_analytics/cli.py

This file contains one Typer command, `run`, plus module-level CLI wiring (`app = typer.Typer(...)`
at L19 and the `if __name__ == "__main__": app()` guard at L57-58). Neither the `Typer()`
instantiation nor the `__main__` guard contains branching or state logic of its own — both are
pure wiring around `run` — so they are covered in `run`'s Purpose/Cross-Function sections rather
than getting their own entries.

---

## `run` in src/port_analytics/cli.py (L22-54)

**Purpose:** The single pipeline entrypoint: pull both Eurostat datasets to disk, transform them
into domain rows/flags, and load them into Azure SQL. It is the only place in the codebase that
wires ingest -> transform -> load together; nothing else in `src/port_analytics/` calls
`pull_and_land`, `transform_all`, and `load_all` in sequence. Without it those three layers exist
but are never composed.

**Inputs & Assumptions:**
- `landing_dir` (`Path`, default `DEFAULT_LANDING_DIR` = `Path("data/raw")`, `config.py:L28`): where
  raw Eurostat responses are written. Trust: semi-trusted — it is a CLI option (Typer exposes every
  parameter of a `@app.command()` function as a CLI flag), so a local operator controls it, but nothing
  downstream validates it against path traversal or an unwritable location; `land_raw_response`
  (`ingest/landing.py:L23`) will raise on `mkdir`/`write_text` failure and that exception is not caught
  here.
- Implicit: process environment (read inside `connect()` via `build_connection_string`,
  `load/connection.py:L28-29`), network reachability to `ec.europa.eu` (via `pull_and_land` ->
  `fetch_dataset`), and the current working directory (relative path resolution for
  `DEFAULT_LANDING_DIR`).
- Implicit: `port_analytics.config.DATASET_CODES` (`config.py:L9-12`) — a two-entry dict whose values
  are the Eurostat dataset codes and whose keys (`"goods_by_direction"`, `"goods_by_cargo_type"`) are
  used as dict lookups at L41-42. Nothing in `run` checks that both keys exist; it is a static module
  constant, not runtime input, so this is a config-shape assumption rather than a trust boundary.
- Precondition: `landed_paths` (from `pull_and_land`) contains, for every value in
  `DATASET_CODES.values()`, exactly one `Path` whose filename starts with that dataset code.
  Established by `pull_and_land` (`ingest/pull.py:L26-28`) and `land_raw_response`
  (`ingest/landing.py:L25`) — see Cross-Function Dependencies; **nothing in `run` itself verifies it**.
- Precondition: required Azure SQL environment variables are set. Established by nothing in `run`;
  checked inside `connect()` -> `build_connection_string` (`load/connection.py:L30-35`), which raises
  `MissingConnectionConfig` rather than returning a sentinel.

**Outputs & Effects:**
- Returns `None` (Typer command; exit code and stdout are the interface).
- Filesystem writes: two JSON files under `landing_dir`, one per dataset (via
  `pull_and_land` -> `land_raw_response`).
- Network: two outbound HTTPS GETs to the Eurostat dissemination API (via `fetch_dataset`).
- Database: schema DDL execution and four rounds of MERGE/UPDATE upserts against Azure SQL (via
  `load_all`), each committed independently (see Block-by-Block on L47-48 and the loader
  cross-function note).
- Console output: six `typer.echo` calls report progress and a final summary.
- No exception handling anywhere in this function. Every exception raised by `pull_and_land`,
  `json.loads`, `transform_all`, `connect`, or `load_all` propagates unmodified out of `run`, through
  Typer/Click, terminating the process with a non-zero exit and a traceback.

**Block-by-Block:**

```python
# L26
landed_paths = pull_and_land(landing_dir=landing_dir)
```
- **What:** Fetches both Eurostat datasets and writes each to disk, raw.
- **Why here:** Must happen before any transform; the transform layer only accepts already-landed
  JSON payloads (`payloads[...]` at L41-42), not live HTTP responses.
- **Assumes:** `pull_and_land` either returns a complete, correctly-named list of two paths or raises.
  There is no partial-success return value to check.
- **Establishes:** nothing directly checkable here — the guarantee (one path per dataset code,
  correctly prefixed) is established two calls down, in `land_raw_response`; see Cross-Function
  Dependencies.
- **Depended on by:** L27-28 (echo), and the `payloads` comprehension at L30-37.

```python
# L30-37
payloads = {
    dataset_code: json.loads(
        next(p for p in landed_paths if p.name.startswith(dataset_code)).read_text(encoding="utf-8")
    )
    for dataset_code in DATASET_CODES.values()
}
```
- **What:** Re-reads each just-landed file from disk (rather than reusing an in-memory payload —
  `pull_and_land` never returns the payload bodies, only paths) and parses it back to a dict, keyed
  by dataset code.
- **Why here:** Bridges the ingest layer (produces file paths) to the transform layer (consumes
  parsed dicts); this is the only place the two are joined.
- **Assumes:** for each `dataset_code`, exactly one entry in `landed_paths` has a filename starting
  with that code. `next(...)` with no default raises `StopIteration` if zero entries match — that
  exception is **not caught inside the comprehension or by `run`**; since this dict comprehension
  runs in a plain (non-generator) function frame, PEP 479's generator-to-`RuntimeError` conversion
  does not apply here, so a `StopIteration` would propagate as-is out of `run`. Also assumes
  `startswith` gives a *unique* match — true today only because `"mar_mg_aa_pwhd"` and
  `"mar_mg_am_pwhc"` are not prefixes of each other (`config.py:L10-11`); nothing enforces that
  property structurally if `DATASET_CODES` is edited later.
- **Establishes:** `payloads` is a dict from dataset code to a parsed JSON-stat body, sourced from
  disk, not from the HTTP response object directly.
- **Depended on by:** L41-42.

```python
# L40-43
rows, flags = transform_all(
    payloads[DATASET_CODES["goods_by_direction"]],
    payloads[DATASET_CODES["goods_by_cargo_type"]],
)
```
- **What:** Runs the full raw-to-domain transform (both datasets plus the Antwerp-Bruges continuity
  derivation) and collects the resulting rows and data-quality flags.
- **Why here:** After both payloads are on hand; before any DB interaction, since `load_all` expects
  fully-built `PortThroughputRow`/`DataQualityFlag` objects, not raw payloads.
- **Assumes:** `DATASET_CODES["goods_by_direction"]` and `DATASET_CODES["goods_by_cargo_type"]` are
  both present as keys — a static assumption about `config.py`, not runtime input; if either key is
  removed, this is a `KeyError` at call time, not a graceful failure.
- **Assumes:** every row `transform_all` returns has a `port_code`/`cargo_type_code` that is a valid
  key for the reference dictionaries `load_all` indexes into later. **Not established by `run`** —
  see Cross-Function Dependencies (`build_direction_rows`, `build_cargo_rows`,
  `derive_pre_merger_antwerp_bruges`).
- **Establishes:** `rows`/`flags` are well-typed (`pydantic`-validated `PortThroughputRow` /
  `DataQualityFlag` instances) — validation happens inside the models' constructors, not in `run`.
- **Depended on by:** L44 (echo count) and L48 (`load_all`).

```python
# L47-48
with connect() as conn:
    summary = load_all(conn, rows, flags)
```
- **What:** Opens an Azure SQL connection and runs the full load (schema, ports, cargo types,
  throughput rows, flags) inside it.
- **Why here:** Last stage; everything loaded is already domain-validated by `transform_all`.
- **Assumes:** `connect()` returns a connection whose context-manager protocol (`__enter__`/`__exit__`)
  correctly finalizes the connection (commit-or-rollback-and-close semantics) whether or not
  `load_all` raises. `pyodbc.Connection` is a compiled extension (`.venv\Lib\site-packages\pyodbc.cp313-win_amd64.pyd`)
  — no Python source is available in this repo to confirm `__exit__` behavior on the exception path;
  see Open Questions.
- **Assumes:** `load_all` is effectively one logical unit of work. **It is not** — see Cross-Function
  Dependencies: each of `apply_schema`, `upsert_ports`, `upsert_cargo_types`, `upsert_throughput_rows`,
  and `upsert_flags` calls `conn.commit()` internally before `load_all` returns or before the next
  step runs, so a failure partway through (e.g., inside `upsert_flags`) leaves the earlier steps'
  writes already committed.
- **Establishes:** on success, `summary` (a `LoadSummary` NamedTuple) reflects counts computed from
  `rows`/`flags`/`port_ids`/`cargo_type_ids` sizes, not from a post-hoc DB read — see loader
  cross-function note on what these counts do and don't verify.
- **Depended on by:** L50-54 (final echo).

```python
# L50-54
typer.echo(
    f"Loaded: {summary.ports_loaded} ports, {summary.cargo_types_loaded} cargo types, "
    f"{summary.throughput_rows_loaded} throughput rows, {summary.flags_loaded} flags "
    f"({summary.revisions_detected} revisions detected this run)."
)
```
- **What:** Prints the run summary.
- **Why here:** Final step; only reachable if every prior stage succeeded without raising.
- **Assumes:** `summary`'s fields are simple `len()` counts (`load/loader.py:L162-166`), not
  post-load verification reads — `throughput_rows_loaded` is `len(rows)` (the input list length),
  not a count of rows actually present in the DB after the MERGE statements ran.
- **Establishes:** nothing further; terminal statement.
- **Depended on by:** nothing — end of function.

**Cross-Function Dependencies:**

- **Callee `pull_and_land` (internal, `ingest/pull.py:L19-29`):** read in full. Loops over
  `DATASET_CODES.values()` (`ingest/pull.py:L26`), calling `fetch_dataset` then
  `land_raw_response` for each. Two paths:
  - Success: returns a list with exactly one `Path` per dataset code, in `DATASET_CODES` iteration
    order — satisfies `run`'s L30-37 precondition.
  - Failure: `fetch_dataset` raises `EurostatAPIError` on any HTTP failure, non-200 status,
    non-JSON body, schema-mismatched body, or zero-datapoint body (`ingest/eurostat_client.py:L69-89`)
    — this exception is not caught anywhere in `pull_and_land` or `run`, so `run` terminates before
    landing the second dataset if the first fetch fails, or before returning at all if the second
    fails after the first already landed a file on disk. That already-landed file is not cleaned up.
- **Callee `land_raw_response` (internal, `ingest/landing.py:L12-27`):** establishes the filename
  convention `run`'s L30-37 depends on — `f"{dataset_code}_{timestamp}.json"` (`landing.py:L25`).
  This is the only place that guarantees the prefix match `payloads` relies on; it is a naming
  convention, not a type-checked contract between the two modules.
- **Callee `transform_all` (internal, `transform/pipeline.py:L14-26`):** read in full, along with
  its own callees:
  - `build_direction_rows` / `build_cargo_rows` (`transform/throughput.py:L37-139`): every emitted
    row's `port_code` is filtered through `if port_code not in PORTS: continue`
    (`throughput.py:L52`, `L109`), and every `cargo_type_code` is either the literal `"TOTAL"`
    (`L58`) or filtered through `cargo_code not in cargo_codes` where `cargo_codes` is derived from
    `CARGO_TYPES` (`L101`, `L109`). This is what establishes, three calls removed from `run`, that
    `load_all`'s `port_ids[row.port_code]` / `cargo_type_ids[row.cargo_type_code]` lookups
    (`load/loader.py:L92-93`) cannot `KeyError` on a row's own code.
  - `derive_pre_merger_antwerp_bruges` (`transform/continuity.py:L43-95`): derived rows always use
    `MERGED_PORT_CODE = "BE_0BE003"` (`continuity.py:L38`, `L85`), which is a `PORTS` key
    (`reference_data.py:L11`). Flags built here (`_merger_summary_flags`,
    `continuity.py:L98-154`) use only `"BE_0BEANR"`, `"BE_0BEZEE"`, and `MERGED_PORT_CODE` as
    `port_code` — all valid `PORTS` keys, which is what makes `upsert_flags`'s
    `port_ids[flag.port_code]` (`load/loader.py:L132`) safe on every path this function can take.
  - All three sub-functions run to completion and cannot themselves fail to reach `transform_all`'s
    return — no exception path was found in `pipeline.py`, `throughput.py`, or `continuity.py` for
    well-formed input; malformed input (missing `payload["dimension"]`/`payload["id"]`/etc.) surfaces
    as a `KeyError` from `decode_observations` (`transform/jsonstat.py:L24-27`), which is not caught
    anywhere in this call chain.
- **Callee `connect` (internal, `load/connection.py:L54-55`):** calls `build_connection_string`,
  which raises `MissingConnectionConfig` (a `RuntimeError` subclass) if any of the four required env
  vars is unset or empty (`load/connection.py:L30-35`) — not caught by `run`. On success, calls
  `pyodbc.connect(...)`; `pyodbc.connect` itself (external, black box — compiled extension, no
  source in this repo) can raise `pyodbc.Error` for authentication/network/driver failures, also
  uncaught here.
- **Callee `load_all` (internal, `load/loader.py:L149-167`):** read in full.
  - `apply_schema` (`L39-43`) executes `schema.sql` verbatim and commits — not read as part of this
    analysis (SQL file, not a function), but note it runs unconditionally on every `run`, including
    against a pre-existing populated database.
  - `upsert_ports`/`upsert_cargo_types` (`L46-73`) build id maps from the **static reference data**
    `PORTS`/`CARGO_TYPES` (`transform/reference_data.py`), not from `rows`/`flags` — so these two
    steps succeed or fail independently of what `transform_all` produced.
  - `upsert_throughput_rows` (`L76-121`) is the step whose safety depends on the transform-layer
    guarantee above; it indexes `port_ids`/`cargo_type_ids` with `row.port_code`/`row.cargo_type_code`
    without a `.get()`-with-default or try/except (`L92-93`) — a `KeyError` here would be uncaught
    by `load_all` and by `run`.
  - `upsert_flags` (`L124-146`) similarly indexes `port_ids[flag.port_code]` unguarded when
    `flag.port_code` is truthy (`L132`); `throughput_ids.get(key)` for the throughput linkage is
    guarded with `.get()` (`L143`) since a missing throughput row is the documented common case for
    `missing_year` flags.
  - Each of the four upsert functions calls `conn.commit()` at its own end (`L60`, `L72`, `L120`,
    `L146`) — `load_all` is a sequence of four independently-committed transactions, not one atomic
    transaction, a fact invisible from `run`'s single `with connect() as conn:` block.
- **Callers:** none — `run` is the Typer command registered on `app` (`L22`) and is the sole
  entrypoint invoked by `app()` at `L58` (module `__main__` guard) or by the `port-analytics` console
  script (per the module docstring, `L1-4`; the script's actual registration was not located in this
  file and was not checked further).
- **Shared state:** none within `cli.py` itself — `run` holds no module-level mutable state; all
  state lives in the filesystem (landing dir), the Eurostat API, and Azure SQL, each touched exactly
  once per `run` invocation.
- **Invariant coupling:** `run`'s correctness rests on a chain of guarantees established two and
  three calls away from it (dataset-code-to-filename prefix matching; row port/cargo codes being
  valid reference-data keys). None of these are re-checked at the point `run` uses them — the
  function is correct only as long as every intermediate layer keeps its side of an implicit,
  untyped contract (string prefix conventions, dict-key membership) rather than a checked one.

**Open Questions:**
- unclear; need to inspect `pyodbc.Connection.__enter__`/`__exit__` semantics (compiled extension at
  `.venv\Lib\site-packages\pyodbc.cp313-win_amd64.pyd`, no source available in this repo) — this
  decides whether an exception raised inside `load_all` (L48) leaves the connection's already-issued,
  already-committed partial writes (see the per-step `conn.commit()` calls in `loader.py`) as the
  final on-disk state, or whether `__exit__` attempts any further rollback of uncommitted work.
- unclear; need to inspect how the `port-analytics` console-script entry point (referenced in the
  module docstring, `L1-4`) is registered — not found in `cli.py` itself; likely in `pyproject.toml`,
  not checked as part of this per-function analysis.
- unclear; need to inspect whether any caller ever passes a `landing_dir` that could collide with
  files from a previous run in a way that changes `land_raw_response`'s append-only guarantee
  (e.g., clock skew producing an identical timestamp) — `land_raw_response`'s own docstring
  (`ingest/landing.py:L20-22`) asserts uniqueness but nothing in that function checks
  `out_path.exists()` before writing (`landing.py:L26`).
