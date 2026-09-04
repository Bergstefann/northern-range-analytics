# `src/port_analytics/config.py`

**No functions are defined in this file.** It is a pure module of top-level constant
bindings (L1-L29): `EUROSTAT_BASE_URL`, `DATASET_CODES`, `PORT_CODES`, `DEFAULT_LANDING_DIR`.
There is no `def`, no class, no `__all__`, no runtime logic beyond attribute/dict/list
literal construction and one `Path(...)` call. Per the task instruction to analyze
"every function in this file," this document instead analyzes the module as a single
initialization unit — each constant is treated as a block, since these bindings are
consumed as trusted configuration by several other modules and any structural
assumption behind them is load-bearing for those callers.

---

## Module `port_analytics.config` in src/port_analytics/config.py (L1-L29)

**Purpose:** Central, single-import source of Eurostat API configuration: the base
REST URL, the two dataset codes this project pulls, the list of Eurostat "rep_mar"
port identifiers to filter on, and the default on-disk landing directory. Every
downstream ingest/CLI module imports from here rather than hardcoding these values,
so a change to a dataset code or port list only has to happen in one place. Without
it, `eurostat_client.py`, `pull.py`, and `cli.py` would each need their own copies of
these values, and the risk of the copies drifting apart (as already appears to have
happened once — see Open Questions on `continuity.py`) would be higher.

**Inputs & Assumptions:**
- No parameters; this is module-level code executed once at first import (Python
  import-caching semantics — CPython runs the module body once per process and
  caches it in `sys.modules`).
- Implicit: the process's current working directory, since `DEFAULT_LANDING_DIR`
  (L28) is a relative `Path`. Nothing in this module or its direct callers anchors it
  to an absolute location (see `DEFAULT_LANDING_DIR` block below).
- Trust level of the data itself: these are hardcoded literals, not read from
  environment, file, or network — so from the perspective of any caller they are
  trusted/fixed at deploy time. There is no runtime input to this module at all.
- Precondition for correctness of every value here: manual accuracy against the live
  Eurostat API. The comment at L7-L8 records that this was already a source of past
  breakage ("the codes in the original build spec ... do not exist") and that the
  current values were hand-verified against the live API rather than derived from
  any schema or spec Eurostat publishes. Nothing in this file, or in any of its
  callers, re-validates these codes against the live API before use — the first
  real validation happens at runtime in `eurostat_client.fetch_dataset`, which raises
  `EurostatAPIError` on a non-200 response or an empty `value` payload (see Cross-
  Function Dependencies).

**Outputs & Effects:**
- Binds four module attributes importable by name. No I/O, no side effects, no
  exceptions possible during import (all literals; `Path(...)` on a plain string
  cannot raise).
- No mutation of any external state.

**Block-by-Block:**

```python
# L5
EUROSTAT_BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
```
- **What:** The fixed base REST endpoint for Eurostat's dissemination API.
- **Why here:** Single point of change if Eurostat's API version/path segment
  (`statistics/1.0/data`) changes.
- **Assumes:** The URL is `https`, well-formed, and reachable; nothing checks this at
  import time.
- **Establishes:** The URL prefix every dataset fetch is built from.
- **Depended on by:** `eurostat_client.fetch_dataset`, which does
  `url = f"{EUROSTAT_BASE_URL}/{dataset_code}"` (eurostat_client.py L64) — string
  concatenation with no validation that the resulting URL is well-formed, no
  trailing-slash normalization, and no escaping of `dataset_code`.

```python
# L9-L12
DATASET_CODES: dict[str, str] = {
    "goods_by_direction": "mar_mg_aa_pwhd",
    "goods_by_cargo_type": "mar_mg_am_pwhc",
}
```
- **What:** Maps a stable, human-meaningful key to the actual Eurostat dataset code
  used in the URL path.
- **Why here:** Keeps the semantic label (`goods_by_direction`) decoupled from the
  Eurostat-assigned code, and centralizes the fact (per the comment at L7-L8) that the
  originally-specified codes (`mar_mg_am_pwhd`, `mar_go_am`) were wrong and had to be
  corrected against the live API.
- **Assumes:** Both codes currently exist and return non-empty JSON-stat data on the
  live Eurostat API; assumes the two logical keys stay in 1:1 correspondence with the
  two codes (nothing enforces exactly two entries, or that the two values are
  distinct).
- **Establishes:** The set of datasets the whole pipeline pulls — this dict's
  `.values()` is the iteration driver for what gets fetched.
- **Depended on by:**
  - `pull.py` L26: `for dataset_code in DATASET_CODES.values(): ... fetch_dataset(dataset_code, PORT_CODES, http)` —
    iterates the dict values with no ordering guarantee beyond Python 3.7+ dict
    insertion order (which does hold here, so `goods_by_direction`'s code is fetched
    before `goods_by_cargo_type`'s, but nothing in `pull.py` depends on that order
    beyond producing `landed` in the same order).
  - `cli.py` L30-L37: builds `payloads` keyed by `dataset_code` (the *value* of
    `DATASET_CODES`, not the key) by matching landed filenames that
    `.startswith(dataset_code)` (cli.py L32). This assumes every code in
    `DATASET_CODES.values()` is a distinct, non-overlapping filename prefix among
    the files actually landed by `pull_and_land` in the same run — see Open
    Questions.
  - `cli.py` L41-L42: re-looks up `payloads[DATASET_CODES["goods_by_direction"]]` and
    `payloads[DATASET_CODES["goods_by_cargo_type"]]` — this assumes both named keys
    (`"goods_by_direction"`, `"goods_by_cargo_type"`) are always present in the dict;
    nothing enforces that beyond the literal at L10-L11 matching what `cli.py`
    hardcodes as string literals independently (no shared constant for the key
    names themselves).

```python
# L14-L18 (comment) / L19-L26
PORT_CODES: list[str] = [
    "BE_0BE003",  # Antwerp-Bruges (merged, 2022+)
    "BE_0BEANR",  # Antwerpen (legacy, through 2021)
    "BE_0BEZEE",  # Zeebrugge (legacy, through 2021)
    "DE_1DEHAM",  # Hamburg
    "NL_0NLRTM",  # Rotterdam
    "PL_0PLGDN",  # Gdansk
]
```
- **What:** The full list of Eurostat `rep_mar` port codes the pipeline filters on,
  including three codes that refer to a single physical merged port
  (Antwerp-Bruges) across the pre/post-2022 boundary.
- **Why here:** Documents, in the accompanying comment, a real-world discontinuity
  (the 2022 Antwerpen/Zeebrugge → Antwerp-Bruges merger) that later transform code
  must handle; keeping all three codes in one list means Eurostat is queried for all
  of them regardless of year, and the year-splitting logic lives downstream.
- **Assumes:** All six codes are valid `rep_mar` values recognized by Eurostat for
  both dataset codes above; assumes there is genuinely "no overlap year" (per L17) —
  i.e., that Eurostat never reports both a legacy code and the merged code for the
  same year. Nothing in this file or its direct callers checks that assumption; it is
  asserted only in the comment and is the empirical premise the whole
  `transform/continuity.py` merger-handling design rests on (see Cross-Function
  Dependencies).
- **Establishes:** The `rep_mar` filter sent on every Eurostat request.
- **Depended on by:** `pull.py` L27 passes this same list, unfiltered and unmodified,
  as the `rep_mar_codes` argument to `fetch_dataset` for *both* dataset codes in the
  same loop iteration set — i.e., both `goods_by_direction` and
  `goods_by_cargo_type` are queried for the exact same six ports. Nothing allows a
  narrower or dataset-specific port list.
- **Notable non-dependency:** `transform/continuity.py` does **not** import
  `PORT_CODES` (or anything else) from this module. It independently hardcodes
  `MERGED_PORT_CODE = "BE_0BE003"` and `LEGACY_PORT_CODES = ("BE_0BEANR", "BE_0BEZEE")`
  (continuity.py L38-L39) as its own string literals. These currently match the first
  three entries of `PORT_CODES` (L20-L22) by value, but there is no shared symbol —
  a future edit to `PORT_CODES` (e.g., renaming or removing the merged code) would
  not propagate to `continuity.py`, and vice versa. See Open Questions.

```python
# L28
DEFAULT_LANDING_DIR = Path("data/raw")
```
- **What:** The default filesystem directory raw Eurostat payloads are written to.
- **Why here:** Single default shared by the CLI entrypoint's default parameter and
  the ingest layer's default parameter, so both agree absent an override.
- **Assumes:** `Path("data/raw")` is interpreted relative to the process's current
  working directory at the time it is actually used (not at import time — `Path`
  does not resolve or touch the filesystem here; `pathlib.Path.__new__` on Windows
  dispatches to a concrete `WindowsPath`/`PureWindowsPath`-based implementation per
  CPython's `pathlib` module, purely string/segment parsing, no I/O). Nothing in this
  file anchors it to an absolute location such as the package root or an
  environment-variable-configured data directory.
- **Establishes:** A default only — it is a mutable default argument at two call
  sites, not enforced as the only possible value.
- **Depended on by:**
  - `cli.py` L23: `def run(landing_dir: Path = DEFAULT_LANDING_DIR) -> None`. Because
    `typer` builds this into a CLI option, in practice the effective directory is
    whatever the process's cwd is when the `port-analytics` command is invoked,
    unless overridden via CLI.
  - `pull.py` L20: `def pull_and_land(landing_dir: Path = DEFAULT_LANDING_DIR, ...)`.
    Same relative-path caveat; `land_raw_response` (landing.py L23) calls
    `landing_dir.mkdir(parents=True, exist_ok=True)` on whatever path it's given, so
    if the two entrypoints (`cli.py`'s `run` and `pull.py`'s `__main__` block) are
    invoked from different working directories, raw data lands in two different
    places on disk despite both nominally using "the default."
- Both `Path` objects created from this default are the *same* object identity
  (Python evaluates `Path("data/raw")` once at import time and both `def` default
  parameters bind to that one object) — `Path` is immutable, so no mutation-of-shared-
  default hazard applies here, unlike the classic mutable-default-argument pitfall
  with lists/dicts.

**Cross-Function Dependencies:**
- Callee `pathlib.Path.__init__`/`__new__` (external, stdlib, source available in
  CPython but treated here as a trusted standard-library primitive): constructs a
  path object from a literal string; no filesystem access, no exceptions raised for
  any string input on this platform.
- Callers (all internal, all importing from this module):
  - `cli.py` (L13) imports `DATASET_CODES`, `DEFAULT_LANDING_DIR`. Assumes
    `DATASET_CODES` has exactly the two keys `"goods_by_direction"` and
    `"goods_by_cargo_type"` (L41-L42) and that every value in `DATASET_CODES` shows
    up as a distinct filename prefix among files actually returned by
    `pull_and_land` (L30-L37) — see Open Questions.
  - `pull.py` (L14) imports `DATASET_CODES`, `DEFAULT_LANDING_DIR`, `PORT_CODES`.
    Assumes `DATASET_CODES.values()` yields the exhaustive set of datasets to fetch,
    and that `PORT_CODES` is the correct, complete filter for every one of those
    datasets (L26-L28).
  - `eurostat_client.py` (L16) imports `EUROSTAT_BASE_URL`. Assumes it is a valid
    URL prefix with no trailing slash (L64 does `f"{EUROSTAT_BASE_URL}/{dataset_code}"`,
    so a trailing slash on the base URL would produce a double slash; there is none
    currently, per L5, but nothing enforces that invariant if the constant is
    edited).
  - `transform/continuity.py`: does not import this module; duplicates
    `"BE_0BE003"`, `"BE_0BEANR"`, `"BE_0BEZEE"` independently (continuity.py
    L38-L39). See Open Questions.
- Shared state: none — this module has no mutable shared state; every consumer reads
  the same immutable literal objects.
- Invariant couplings: the "no overlap year" empirical claim at L17 (comment only) is
  the load-bearing assumption behind `continuity.py`'s merger logic, specifically the
  filter `row.year >= ANTWERP_BRUGES_MERGER_YEAR` (continuity.py L50), which decides
  whether a legacy-coded row is included in the derived pre-merger sum. That filter's
  correctness depends on `ANTWERP_BRUGES_MERGER_YEAR` (defined in
  `transform/reference_data.py`, not read here) agreeing with whatever year Eurostat
  actually stops publishing legacy codes for — a fact this module documents in prose
  (L14-L18) but does not encode as a checkable value.

**Open Questions:**
- `cli.py` L30-L37 matches landed files to dataset codes via
  `p.name.startswith(dataset_code)` where `dataset_code` ranges over
  `DATASET_CODES.values()`. If one dataset code were ever a string-prefix of another
  (not currently the case: `"mar_mg_aa_pwhd"` vs `"mar_mg_am_pwhc"` diverge at
  character 6), `next(...)` would deterministically pick whichever file sorts first
  in `landed_paths` order for the shorter-prefix code, silently pairing the wrong
  payload with the wrong dataset key. Nothing in `config.py` or `cli.py` enforces
  "no dataset code is a prefix of another." Need to inspect whether this is asserted
  or tested anywhere.
- `transform/continuity.py` hardcodes port codes that duplicate three of the six
  entries in `PORT_CODES` (L20-L22) by value but not by reference. Unclear whether
  there is a project convention (docs/data-quality-notes.md, not read as part of this
  analysis) that declares `config.py` as authoritative for port codes generally, or
  whether the merger-specific codes are intentionally treated as a separate, stable
  contract independent of the queryable port list. Need to inspect
  docs/data-quality-notes.md and any tests asserting these stay in sync.
- `DEFAULT_LANDING_DIR` is a relative path (L28) with no code anywhere (config.py,
  cli.py, pull.py) resolving it against the package/repo root or an env var. Need to
  inspect deployment/run instructions (README, CI config) to determine what working
  directory the process is actually expected to run from, since a mismatch between
  `cli.py`'s invocation cwd and `pull.py`'s (if ever run standalone via
  `python -m port_analytics.ingest.pull`, per its own L1-L5 docstring) would land
  data in two different directories without either path raising an error.
- Nothing in this file or its direct callers re-validates `DATASET_CODES` or
  `PORT_CODES` against the live Eurostat API before use; the first real check is
  `eurostat_client.fetch_dataset`'s HTTP-status and empty-`value` check
  (eurostat_client.py L72-L73, L88-L89). Whether that is considered sufficient
  "fail loudly" coverage for a wrong-but-still-200-with-empty-body response, versus a
  wrong code that happens to return a differently-shaped but non-empty payload, is
  unclear without inspecting Eurostat's actual error-response behavior for unknown
  dataset codes.
