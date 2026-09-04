# Function analyses: src/port_analytics/transform/throughput.py

Module purpose (from docstring, L1-5): builds `port_throughput` rows from two raw Eurostat
JSON-stat payloads and flags gaps in (port, cargo_type, direction) coverage, excluding the
Antwerp-Bruges merger case which is handled in `transform/continuity.py`. Five functions total:
two public builders (`build_direction_rows`, `build_cargo_rows`) and three private helpers
(`_all_years`, `_eligible_years`, `_gap_flags`) shared by both builders.

---

## `build_direction_rows` in src/port_analytics/transform/throughput.py (L37-82)

**Purpose:** Converts the `mar_mg_aa_pwhd` Eurostat dataset (gross weight by port and direction,
no cargo breakdown) into `PortThroughputRow` objects plus `DataQualityFlag` rows describing gaps
in expected coverage. This is one of the two entry points the whole module exists to serve; if it
mis-decodes or mis-filters, every `port_throughput` row sourced from this dataset is wrong or
silently absent.

**Inputs & Assumptions:**
- `payload` (dict[str, Any]): raw JSON-stat 2.0 response for `mar_mg_aa_pwhd`. Trust:
  **semi-trusted external data** — sourced from the Eurostat API, not attacker-controlled in the
  usual sense, but not schema-validated before this function runs. No assertion anywhere in this
  function (or its callees) verifies the payload's overall shape beyond what dict indexing
  requires at each access point.
- Implicit: none beyond `payload` itself — no clock, no caller identity, no ambient state.
- Precondition: `payload["dimension"]["time"]["category"]["index"]` exists and is a
  `dict[str, int]` of year strings — required by `_all_years` (L143), not checked here.
- Precondition: `payload["id"]`, `payload["size"]`, `payload["dimension"]`, `payload["value"]`
  all exist and are internally consistent (see `decode_observations` analysis below) — required
  by L46, not checked here.
- Precondition (implicit domain contract, unenforced): the payload's dimension ids include
  `"unit"`, `"rep_mar"`, `"direct"`, `"time"` using exactly those names, because L48/L50/L51/L54
  read `dv.get("unit")`, `dv.get("rep_mar")`, `dv.get("direct")`, `dv["time"]`. Nothing in this
  file or `decode_observations` validates that these keys exist in the decoded
  `dimension_values`; a renamed or missing dimension id degrades silently (see Block-by-Block).

**Outputs & Effects:**
- Returns `(rows, flags)`: `rows` is every accepted observation converted to a
  `PortThroughputRow`; `flags` is a complete `PORTS × DIRECTION_CODES` matrix of gap
  classifications (L67-80), computed independent of how many rows were actually produced.
- No mutation of `payload` or any external state. No I/O.
- Postcondition: for every `(port_code, direct_code)` pair, either at least one row exists for
  each eligible year, or a flag explains the gap (established by the loop at L68-80, contingent
  on `_gap_flags` — see that function).

**Block-by-Block:**

```python
# L46-L49
for obs in decode_observations(payload):
    dv = obs.dimension_values
    if dv.get("unit") != "THS_T":
        continue
```
- **What:** Iterates all decoded observations and keeps only those measured in thousand tonnes.
- **Why here:** First filter, before any domain-code lookups, so downstream logic only sees the
  measure this row type expects.
- **Assumes:** the payload's `unit` dimension uses `THS_T` for the value comparable and always
  appears as `dv["unit"]` under that exact key. If the dimension id or code differs,
  `dv.get("unit")` returns `None`, the filter silently drops *every* observation, and the
  function returns `rows=[]` with a full matrix of `missing_year`/`code_change` flags at L67-80 —
  indistinguishable, from the caller's side, from a payload that genuinely has no `THS_T` data.
- **Establishes:** `obs.value` is in thousand tonnes for every surviving observation — depended
  on by the `* THOUSAND_TONNES_TO_TONNES` conversion at L61.
- **Depended on by:** L61 (unit conversion correctness).

```python
# L50-L53
port_code = dv.get("rep_mar")
direct_code = dv.get("direct")
if port_code not in PORTS or direct_code not in DIRECTION_CODES:
    continue
```
- **What:** Resolves the port and direction dimension codes and drops any observation whose code
  isn't in the curated `PORTS` / `DIRECTION_CODES` reference tables.
- **Why here:** After the unit filter, before the row is built, so only recognized
  port/direction combinations reach `PortThroughputRow` construction.
- **Assumes:** `PORTS` and `DIRECTION_CODES` (reference_data.py L10-27, L48-52) are a complete
  and current allowlist. Eurostat adding a new reporting port or a new direction code is
  indistinguishable here from noise — the observation is silently dropped, with no flag or log
  recording that a code was seen and rejected (contrast with the `missing_year`/`code_change`
  flags, which only fire for codes already known to `PORTS`).
- **Establishes:** `port_code in PORTS` and `direct_code in DIRECTION_CODES` for the rest of the
  loop body.
- **Depended on by:** L56-64 (row construction), L65 (`presence` keying).

```python
# L54
year = int(dv["time"])
```
- **What:** Parses the time dimension value as an integer year.
- **Why here:** Needed for both the row (L59) and the presence-tracking key (L65).
- **Assumes:** `"time"` is present in `dv` (unchecked — direct indexing, not `.get`) and its
  value is an integer-parseable string. Both properties come from `decode_observations`
  including `"time"` in `payload["id"]` and the corresponding category index having numeric-
  looking keys; neither is verified in this function. A payload missing the `time` dimension
  from `id`, or with non-numeric time category keys, raises an uncaught `KeyError` /
  `ValueError` here.
- **Establishes:** `year` used consistently as an `int` across `rows` and `presence`.
- **Depended on by:** L59, L65, and downstream `_eligible_years`/`_gap_flags` (comparison against
  `all_years`, which is also int-typed via `_all_years`).

```python
# L55-L65
rows.append(
    PortThroughputRow(
        port_code=port_code,
        cargo_type_code="TOTAL",
        year=year,
        direction=DIRECTION_CODES[direct_code],
        gross_weight_tonnes=obs.value * THOUSAND_TONNES_TO_TONNES,
        source=DIRECTION_DATASET_CODE,
    )
)
presence[(port_code, direct_code)].add(year)
```
- **What:** Builds the output row (cargo type hardcoded to `"TOTAL"` per this dataset's shape,
  per the module docstring at L40-41) and records that this port/direction/year combination was
  observed.
- **Why here:** After all filters and validation, so every constructed row is well-formed.
- **Assumes:** `obs.value` is already restricted to `THS_T` observations by the L48 filter, so
  the flat `* 1000` conversion (L34, `THOUSAND_TONNES_TO_TONNES`) is unconditionally correct for
  every row reaching this point.
- **Establishes:** `presence[(port_code, direct_code)]` accumulates the set of years actually
  seen for that pair — this is the sole input, besides `_eligible_years`, to gap detection.
- **Depended on by:** L77 (`presence[(port_code, direct_code)]` passed into `_gap_flags`).

```python
# L67-L80
flags: list[DataQualityFlag] = []
for port_code in PORTS:
    eligible = _eligible_years(port_code, all_years)
    for direct_code, direction in DIRECTION_CODES.items():
        flags.extend(
            _gap_flags(
                port_code, "TOTAL", direction, eligible,
                presence[(port_code, direct_code)], DIRECTION_DATASET_CODE,
            )
        )
```
- **What:** For every port and every direction code (including `"TOTAL"` itself, since
  `DIRECTION_CODES` contains a `TOTAL` entry — L48-52 of reference_data.py), computes and
  collects gap flags.
- **Why here:** Runs after the full observation loop so `presence` is complete; iterates over the
  static reference tables rather than over what was actually observed, so the flag set is
  exhaustive regardless of how sparse or empty `rows` turned out to be.
- **Assumes:** `presence[(port_code, direct_code)]` — a `defaultdict(set)` — safely returns an
  empty set for any pair never populated at L65 (relies on `defaultdict`'s default-factory
  behavior, not an explicit check).
- **Establishes:** the postcondition described above (every eligible year is either represented
  in `rows` or explained by a flag), contingent entirely on `_gap_flags`'s classification logic
  being correct — see that function's analysis.
- **Depended on by:** the caller (`transform_all` in pipeline.py) and ultimately the data-quality
  reporting layer downstream of this module.

**Cross-Function Dependencies:**
- Callee `decode_observations` (internal, transform/jsonstat.py L23-50): read in full. Purely
  positional/index-math decoding of the JSON-stat flat value map; see its own analysis below for
  the preconditions it assumes about `payload` but does not check (dimension/size consistency,
  non-zero sizes, numeric flat-index keys). `build_direction_rows` inherits every one of those
  unchecked preconditions — a malformed `payload` propagates an uncaught exception up through
  this function with no handling here.
- Callee `_all_years` (internal, L142-144): establishes the full candidate year list used both
  for eligibility (L69) and — indirectly — nothing else in this function (the loop at L46 does
  not use `all_years` at all, only the per-year value from each observation). Reads
  `payload["dimension"]["time"]["category"]["index"]` directly, bypassing `decode_observations`
  entirely; this is a second, independent parse of the same payload region, and nothing enforces
  that its notion of "time" agrees with the `"time"` dimension id `decode_observations` uses at
  L54 beyond both reading the same `payload["dimension"]["time"]` structure.
- Callee `_eligible_years` (internal, L147-157): establishes, per port, which years are expected
  to be reportable given the Antwerp-Bruges merger cutover. Depended on to scope gap detection
  correctly — see its own analysis for the two-tier `merged_into` logic.
- Callee `_gap_flags` (internal, L160-244): establishes the actual flag classification
  (`code_change` vs `missing_year`). `build_direction_rows` depends on it for correctness of the
  entire flags half of its output; see its own analysis for the edge cases in the contiguity
  check.
- Callers: `transform_all` (pipeline.py L18) calls this directly with `direction_payload`,
  concatenates `direction_rows` into `reported_rows` (pipeline.py L21) which is then fed to
  `derive_pre_merger_antwerp_bruges` (continuity.py). That function assumes `PortThroughputRow`
  values for `BE_0BEANR`/`BE_0BEZEE` with `year < 2022` are trustworthy sums-in-waiting
  (continuity.py L50-53) — it has no way to detect if this function's unit/code filtering
  silently dropped legitimate data (see the L48 and L50-53 assumptions above).
- Shared state: none — `PORTS`, `DIRECTION_CODES`, `CARGO_TYPES` are read-only module-level
  dicts from `reference_data.py`, not mutated by this or any other function reviewed here.

**Open Questions:**
- unclear; need to inspect whether an extract/fetch layer upstream of `transform_all` validates
  `payload`'s shape (presence of `"id"`, `"dimension"`, `"value"`, `"size"` keys, and dimension
  id names) before it reaches `build_direction_rows`. If not, every unchecked precondition listed
  above is reachable from a live API response change.
- unclear; need to inspect whether any logging or metrics exist elsewhere in the pipeline that
  would surface "N observations silently dropped by unit/code filter" — nothing in this file
  does.

---

## `build_cargo_rows` in src/port_analytics/transform/throughput.py (L85-139)

**Purpose:** Converts the `mar_mg_am_pwhc` Eurostat dataset (gross weight by port and cargo type,
no direction breakdown) into `PortThroughputRow` objects plus gap flags, deliberately excluding
`cargo='TOTAL'` (to avoid a duplicate total already supplied by `build_direction_rows`) and
`cargo='UNK'` (documented as a phantom, zero-data category). Structurally a near-duplicate of
`build_direction_rows` with cargo in place of direction.

**Inputs & Assumptions:**
- `payload` (dict[str, Any]): raw JSON-stat 2.0 response for `mar_mg_am_pwhc`. Same trust level
  and same unchecked-precondition profile as `build_direction_rows`'s `payload` — see that
  section; every point there about `decode_observations`, `_all_years`, and dimension-id naming
  applies identically here with `"cargo"` in place of `"direct"`.
- Precondition (implicit, unenforced): `CARGO_TYPES` (reference_data.py L38-46) does **not**
  contain an `"UNK"` entry at all. The module docstring (L94-96) frames the `cargo_code not in
  cargo_codes` check as "defensive" against `UNK` rows, but `UNK` is excluded from `CARGO_TYPES`
  itself, not merely from `cargo_codes` — so this function's filter is not what keeps `UNK`
  out: if `UNK` were added to `CARGO_TYPES` as anything other than `"TOTAL"`, the filter would
  *include* it, since `cargo_codes` is `CARGO_TYPES` minus `"TOTAL"` (L101). The real
  UNK-exclusion mechanism is `CARGO_TYPES` never defining the key, established in
  reference_data.py, not in this function.

**Outputs & Effects:**
- Returns `(rows, flags)` with the same shape and construction pattern as `build_direction_rows`,
  scoped to `PORTS × (CARGO_TYPES - {"TOTAL"})` (L125-137) instead of `PORTS × DIRECTION_CODES`.
- Every row has `direction=Direction.TOTAL` (L117) — hardcoded, mirroring the hardcoded
  `cargo_type_code="TOTAL"` in `build_direction_rows` (L58).
- No mutation of `payload`, no I/O.

**Block-by-Block:**

```python
# L101
cargo_codes = [code for code in CARGO_TYPES if code != "TOTAL"]
```
- **What:** Computes the set of cargo codes this dataset is expected to report, excluding the
  synthetic `TOTAL` category.
- **Why here:** Computed once before the observation loop, used both as a membership filter
  (L109) and as the iteration set for flag generation (L127).
- **Assumes:** `CARGO_TYPES`'s keys are exactly the cargo codes Eurostat uses for this dataset,
  aside from `TOTAL`. Any cargo code Eurostat reports that isn't in `CARGO_TYPES` is silently
  dropped at L109, with no record distinguishing "no data for cargo X" from "cargo X is unknown
  to this codebase."
- **Establishes:** the exact same `cargo_codes` list used at L109 (filter) and L127 (flag
  iteration) — a single source of truth, so filter and flag-scope can't drift apart from each
  other, only from Eurostat's actual code set.
- **Depended on by:** L109, L127.

```python
# L105-L110
if dv.get("unit") != "THS_T":
    continue
port_code = dv.get("rep_mar")
cargo_code = dv.get("cargo")
if port_code not in PORTS or cargo_code not in cargo_codes:
    continue
```
- **What:** Same two-stage filter as `build_direction_rows` L48-53, substituting `"cargo"` for
  `"direct"`.
- **Why here / Assumes / Establishes / Depended on by:** identical reasoning to the
  `build_direction_rows` analysis of L48-53 — see that section. The same silent-drop-on-rename
  and silent-drop-on-unknown-code behaviors apply here with the `"cargo"` dimension id.

```python
# L112-L122
rows.append(
    PortThroughputRow(
        port_code=port_code, cargo_type_code=cargo_code, year=year,
        direction=Direction.TOTAL,
        gross_weight_tonnes=obs.value * THOUSAND_TONNES_TO_TONNES,
        source=CARGO_DATASET_CODE,
    )
)
presence[(port_code, cargo_code)].add(year)
```
- **What:** Builds the row and records presence, keyed by `(port_code, cargo_code)` rather than
  `(port_code, direct_code)`.
- **Why here / Assumes / Establishes:** identical pattern to `build_direction_rows` L55-65.
- **Depended on by:** L134 (`presence[(port_code, cargo_code)]` passed into `_gap_flags`).

```python
# L124-L137
flags: list[DataQualityFlag] = []
for port_code in PORTS:
    eligible = _eligible_years(port_code, all_years)
    for cargo_code in cargo_codes:
        flags.extend(
            _gap_flags(port_code, cargo_code, Direction.TOTAL, eligible,
                       presence[(port_code, cargo_code)], CARGO_DATASET_CODE)
        )
```
- **What:** Exhaustive `PORTS × cargo_codes` gap-flag matrix, mirroring
  `build_direction_rows` L67-80.
- **Assumes / Establishes / Depended on by:** identical to `build_direction_rows`'s equivalent
  block; the correctness of the flags rests entirely on `_gap_flags` and `_eligible_years`.

**Cross-Function Dependencies:**
- Same callees as `build_direction_rows`: `decode_observations`, `_all_years`,
  `_eligible_years`, `_gap_flags` — see those analyses; nothing differs in how this function uses
  them beyond the dimension id (`"cargo"`) and the reference table (`CARGO_TYPES` /
  `cargo_codes`) substituted in.
- Callers: `transform_all` (pipeline.py L19) calls this with `cargo_payload`, concatenates
  `cargo_rows` into `reported_rows` (pipeline.py L21). `derive_pre_merger_antwerp_bruges`
  (continuity.py) consumes rows from *both* builders indiscriminately via `rows.port_code` /
  `rows.cargo_type_code` — it has no way to tell a `build_cargo_rows`-sourced row from a
  `build_direction_rows`-sourced row apart from `source` (L119 vs L62), and does not
  discriminate on `source` at all (continuity.py L50-53 only checks `port_code` and `year`). This
  means `build_cargo_rows`'s `cargo_type_code` values (e.g. `"LBK"`, `"DBK"`) and
  `build_direction_rows`'s fixed `"TOTAL"` both flow into the same `values_by_key` dict in
  continuity.py, keyed in part by `cargo_type_code` — so they don't collide with each other
  there, but both builders' correctness is a precondition `derive_pre_merger_antwerp_bruges`
  takes on faith.
- Invariant coupling: this function's exclusion of `cargo='TOTAL'` (L91-96 docstring, enforced at
  L101) is load-bearing for avoiding a duplicate `(port, TOTAL, year)` row against
  `build_direction_rows`'s output — nothing re-checks this at the `pipeline.transform_all` level;
  it is enforced solely by this one list comprehension.

**Open Questions:**
- unclear; need to inspect docs/data-quality-notes.md "Finding 2" (referenced at L95) to confirm
  the claim that `UNK` has zero data points is still current, since nothing in code re-verifies
  it — the claim is architecturally assumed, not runtime-checked.

---

## `_all_years` in src/port_analytics/transform/throughput.py (L142-144)

**Purpose:** Extracts the full sorted list of years declared in the payload's `time` dimension
category index. This is the "expected" year universe both builders use to compute eligibility
and, from there, gaps — everything downstream of it in gap detection inherits whatever this
function returns.

**Inputs & Assumptions:**
- `payload` (dict[str, Any]): same payload as passed to the calling builder. Trust: semi-trusted
  external data, same as above.
- Precondition: `payload["dimension"]["time"]["category"]["index"]` exists and is a
  `dict[str, int]`-shaped mapping of year-string keys. **Nothing checks this** — direct chained
  indexing (L143) raises `KeyError` on any missing level.
- Precondition (unenforced): every key in that index is parseable as `int` (L144) — a
  non-numeric key raises `ValueError`.

**Outputs & Effects:**
- Returns a sorted `list[int]` of years. No state writes, no I/O, pure function of `payload`.

**Block-by-Block:**

```python
# L142-L144
def _all_years(payload: dict[str, Any]) -> list[int]:
    time_index: dict[str, int] = payload["dimension"]["time"]["category"]["index"]
    return sorted(int(year) for year in time_index)
```
- **What:** Reads the JSON-stat `time` dimension's category index and returns its keys as a
  sorted list of ints.
- **Why here:** This is a second, independent path into the same `payload["dimension"]`
  structure that `decode_observations` also reads generically for every dimension in
  `payload["id"]` (jsonstat.py L31). The two are not reconciled: this function assumes `"time"`
  is one of the dimension ids without checking `payload["id"]` at all.
- **Assumes:** the `time` category index's keys are exactly the calendar years the dataset
  claims to cover, and that they form the year universe consumers should treat as "expected."
  Nothing establishes that this set is contiguous (no gaps in the integer sequence) — later
  contiguity assumptions in `_gap_flags` inherit whatever shape this list has.
- **Establishes:** the `all_years` value both builders pass into `_eligible_years` — the sole
  root of the "what years should exist" side of gap detection.
- **Depended on by:** `_eligible_years` (L147 signature), and transitively `_gap_flags`'s
  contiguity checks (L185, L206).

**Cross-Function Dependencies:**
- No callees.
- Callers: `build_direction_rows` (L42), `build_cargo_rows` (L98). Both call it exactly once per
  payload and treat the result as authoritative for the entire flag-generation matrix.

**Open Questions:**
- unclear; need to inspect a real `mar_mg_aa_pwhd`/`mar_mg_am_pwhc` JSON-stat response to confirm
  the `time` category index is always calendar-contiguous (no skipped years) — if it can have
  gaps, the "clean cutoff" contiguity logic in `_gap_flags` (see below) is checking list-position
  contiguity, not calendar contiguity, and the two would silently diverge.

---

## `_eligible_years` in src/port_analytics/transform/throughput.py (L147-157)

**Purpose:** Scopes the "expected to report" year range per port, accounting for the
Antwerp-Bruges merger: a legacy port (`merged_into` set) is expected only pre-merger, the merged
entity is expected only post-merger, and every other port is expected across the full range. This
is the mechanism by which the merger is kept out of `missing_year`/`code_change` flag noise, per
the module's stated design (L1-4, L148-151).

**Inputs & Assumptions:**
- `port_code` (str): trusted — always a key of `PORTS`, since both callers iterate
  `for port_code in PORTS` (L68, L125) before calling this.
- `all_years` (list[int]): trusted within this function's contract, but see `_all_years`'s open
  question about whether it's calendar-contiguous — that assumption is inherited, not
  re-verified here.
- Implicit: `PORTS` (reference_data.py L10-27) and `ANTWERP_BRUGES_MERGER_YEAR = 2022`
  (reference_data.py L57), both static module-level constants.

**Outputs & Effects:**
- Returns a `list[int]` subset of `all_years`. Pure function, no side effects.

**Block-by-Block:**

```python
# L152-L157
port = PORTS[port_code]
if port.merged_into is not None:
    return [y for y in all_years if y < ANTWERP_BRUGES_MERGER_YEAR]
if any(p.merged_into == port_code for p in PORTS.values()):
    return [y for y in all_years if y >= ANTWERP_BRUGES_MERGER_YEAR]
return list(all_years)
```
- **What:** Three-way branch: legacy port → pre-merger years only; merge target → post-merger
  years only; everyone else → all years.
- **Why here:** `port.merged_into is not None` is checked first, so a port that is *both* a
  legacy port (has `merged_into` set) and — hypothetically — also referenced as someone else's
  `merged_into` target would be classified only as legacy (first branch wins). Not reachable with
  the current `PORTS` data (only `BE_0BE003` is a merge target and it has `merged_into=None`),
  but the precedence is a property of this code, not of the data, and nothing in `Port`
  (models.py L31-37) prevents a future entry from having both a non-`None` `merged_into` and
  being some other port's target.
- **Assumes:** `ANTWERP_BRUGES_MERGER_YEAR` correctly marks the first post-merger year with "no
  overlap year" (per reference_data.py L54-56 comment) — i.e., no year is simultaneously valid
  for both legacy and merged reporting. This function trusts that comment; it does not derive or
  check it against `all_years`.
- **Establishes:** the `eligible` year list each builder passes into `_gap_flags` — the second of
  the two inputs (along with `presence`) that determine every flag emitted.
- **Depended on by:** `_gap_flags`'s entire contiguity/classification logic, and by extension
  every `code_change`/`missing_year` flag in the module's output.

**Cross-Function Dependencies:**
- No callees beyond dict/attribute access on `PORTS`.
- Callers: `build_direction_rows` (L69), `build_cargo_rows` (L126) — both call this once per port
  per builder invocation (so twice per port across the full pipeline, once per dataset), and both
  trust the result completely with no re-validation.
- Invariant coupling: this function is the *only* place the Antwerp-Bruges merger is factored
  into gap detection within this module; `continuity.py`'s derivation logic is a separate,
  independent computation over the same `ANTWERP_BRUGES_MERGER_YEAR` constant (continuity.py
  L50). The two modules share the constant but not the logic — a change to merger semantics that
  updates one and not the other would desynchronize gap-flagging from continuity-derivation
  without either module signaling the mismatch.

**Open Questions:**
- unclear; need to inspect whether any port could ever have `merged_into` pointing at a port
  that itself has `merged_into` set (a merge chain) — `_eligible_years` does not follow chains
  (single dict lookup at L152), so a two-level merger would silently misclassify the
  intermediate port under the current logic. Not reachable with today's `PORTS` data.

---

## `_gap_flags` in src/port_analytics/transform/throughput.py (L160-244)

**Purpose:** Classifies the gap in one `(port, cargo_type, direction)` coverage series into
either a single `code_change` flag (clean cutoff at the start or end of the eligible range) or
one `missing_year` flag per missing year (anything else, per the docstring L168-174). This is the
sole flag-shaping logic in the module — both builders delegate all classification decisions here.

**Inputs & Assumptions:**
- `port_code`, `cargo_type_code` (str): trusted — always valid keys into `PORTS` /
  `CARGO_TYPES` by construction of the two call sites (L74/L131, both from static iteration over
  those same dicts, with `"TOTAL"` hardcoded at the `build_direction_rows` call site).
- `direction` (Direction): trusted enum value, sourced from `DIRECTION_CODES.values()` or
  `Direction.TOTAL` directly.
- `eligible_years` (list[int]): from `_eligible_years` — see that function's assumptions,
  inherited here. Not required to be pre-sorted by the caller (this function re-sorts at L176),
  but *is* implicitly assumed calendar-contiguous by the "clean cutoff" logic below.
- `present_years` (set[int]): from the builder's `presence` dict, possibly empty (`defaultdict`
  default) if nothing was observed for this key — see both builders' L48/L105 filtering
  discussion for how it can legitimately end up empty even when the payload had relevant data.
- `source` (str): trusted, one of the two module-level dataset code constants.

**Outputs & Effects:**
- Returns `list[DataQualityFlag]`: `[]` if no gap, `[one CODE_CHANGE flag]` if the gap is a clean
  edge cutoff, or `[one MISSING_YEAR flag per missing year]` otherwise. Pure function, no side
  effects, no exceptions raised on any of its own logic paths (all list indexing is guarded by
  the `tail_len < len(sorted_years)` conditions — see below).

**Block-by-Block:**

```python
# L176-L179
sorted_years = sorted(eligible_years)
missing = [y for y in sorted_years if y not in present_years]
if not missing:
    return []
```
- **What:** Computes the missing-years list (order preserved as a subsequence of
  `sorted_years`) and short-circuits when there's no gap.
- **Why here:** Establishes `missing` as non-empty for every remaining branch, so the later
  `missing[0]`/`missing[-1]` accesses (L193, L214, L199, L220) are always safe.
- **Assumes:** `present_years not in eligible_years` years are irrelevant — the function only
  ever looks at presence *within* the eligible set; a year present in `present_years` but outside
  `eligible_years` (e.g. a legacy port reporting one year past its merger cutoff) is simply never
  considered, neither as coverage nor as an anomaly. No flag would ever record such an
  out-of-window observation.
- **Establishes:** `missing` non-empty for L183 onward.
- **Depended on by:** every subsequent line in the function.

```python
# L183-L204
tail_len = len(missing)
if tail_len < len(sorted_years) and sorted_years[-tail_len:] == missing:
    last_present = sorted_years[-tail_len - 1]
    return [DataQualityFlag(flag_type=FlagType.CODE_CHANGE, ...)]
```
- **What:** Detects whether `missing` is exactly the trailing slice of `sorted_years` (reporting
  stopped and never resumed) and, if so, emits one `code_change` flag rather than per-year flags.
- **Why here:** Checked before the "scattered" fallback so a clean trailing gap is classified
  specially; guarded by `tail_len < len(sorted_years)` so `last_present`'s index
  (`-tail_len - 1`) never underflows past index 0 and the all-missing case falls through instead.
- **Assumes:** equality between `sorted_years[-tail_len:]` and `missing` is being used as a proxy
  for "calendar-contiguous trailing gap." This is only true if `sorted_years` (i.e.
  `eligible_years`, i.e. ultimately `all_years` from `_all_years`) is itself calendar-contiguous
  with no skipped years — a property asserted nowhere in this module (see `_all_years`'s open
  question). If `eligible_years` has an internal gap (say a year absent from the JSON-stat
  `time` category index entirely, not merely absent from `value`), a `missing` set that is a
  trailing *list-position* slice but not a trailing *calendar* block would still be classified
  `code_change` and the flag's description text ("Clean one-directional cutoff... through
  {last_present}") would assert calendar contiguity the data doesn't actually establish.
- **Establishes:** early return; this port/cargo/direction key is fully resolved.
- **Depended on by:** nothing further in this function (terminal on this path).

```python
# L206-L225
if tail_len < len(sorted_years) and sorted_years[:tail_len] == missing:
    first_present = sorted_years[tail_len]
    return [DataQualityFlag(flag_type=FlagType.CODE_CHANGE, ...)]
```
- **What:** Mirror of the previous block for a *leading* gap (reporting started late).
- **Why here / Assumes / Establishes:** symmetric to the trailing-gap block above; the same
  list-position-vs-calendar-contiguity assumption applies. Note both this check and the trailing
  one are evaluated unconditionally in sequence (not `elif`) — if `missing` somehow satisfied
  both (only possible when `tail_len == len(sorted_years)`, which the guard excludes), only the
  first (trailing) branch would ever fire, since it returns first. Not reachable given the guard,
  but worth noting the ordering determines precedence in principle.
- **Depended on by:** nothing further (terminal on this path).

```python
# L227-L244
return [
    DataQualityFlag(flag_type=FlagType.MISSING_YEAR, ..., throughput_ref=ThroughputRef(...))
    for year in missing
]
```
- **What:** Fallback: one `missing_year` flag per missing year, each carrying a `throughput_ref`
  naming the exact `(port, cargo_type, year, direction)` the row would have occupied.
- **Why here:** Reached only when neither edge-cutoff pattern matched — covers both genuinely
  scattered gaps and contiguous gaps that sit in the *middle* of the eligible range (a contiguous
  mid-range gap is not "scattered" in the intuitive sense the docstring (L174) uses, but the code
  treats it identically to scattered single years, since it touches neither list edge).
- **Assumes:** every `year in missing` is a valid `int` usable as `ThroughputRef.year` (guaranteed
  by construction from `eligible_years`/`_all_years`).
- **Establishes:** the per-year flag record downstream consumers rely on to know exactly which
  `(port, cargo_type, year, direction)` combination has no row and no other explanation.

**Cross-Function Dependencies:**
- No callees (pure logic over its arguments and the `PORTS`/`CARGO_TYPES` name lookups at
  L181-182, both guaranteed valid by caller contract — see Inputs).
- Callers: `build_direction_rows` (L71-80), `build_cargo_rows` (L128-137) — both call this once
  per `(port, direction-or-cargo)` pair and simply `.extend()` the result into the aggregate
  `flags` list with no post-processing or validation of what came back.
- Invariant coupling: the correctness of every `code_change` vs `missing_year` classification in
  the entire module's output rests on `eligible_years` being calendar-contiguous, a property
  established (if at all) only by the shape of Eurostat's `time` dimension category index, three
  function calls upstream (`_all_years` → `_eligible_years` → here), and never checked at any
  point in between.

**Open Questions:**
- unclear; need to inspect whether a port that reports in a year *outside* its `_eligible_years`
  window (e.g., a legacy Antwerp/Zeebrugge code reporting one year after the merger cutoff) is
  meant to be silently ignored by this function, as it currently is (see L176-179 analysis), or
  whether that scenario should itself produce a flag — nothing in the module's stated design
  (L1-5, L148-151) addresses reporting *outside* the eligible window, only gaps *within* it.
