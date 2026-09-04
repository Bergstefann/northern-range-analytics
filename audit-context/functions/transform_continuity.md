# Function analyses: src/port_analytics/transform/continuity.py

Module purpose (from the module docstring, L1-25): Eurostat never published a unified
pre-2022 Antwerp-Bruges figure. This module leaves the raw per-legacy-port rows
(BE_0BEANR, BE_0BEZEE) untouched and *additionally* derives a synthetic pre-2022
BE_0BE003 series by summing the two legacy ports per (cargo_type, direction, year),
tagging every derived row `source='derived_sum:...'` and backing the decision with
`data_quality_flags` rows.

---

## `derive_pre_merger_antwerp_bruges` in src/port_analytics/transform/continuity.py (L43-95)

**Purpose:** Produces the derived pre-2022 BE_0BE003 throughput rows (Antwerpen +
Zeebrugge summed per cargo_type/direction/year) plus the data-quality flags that
document and justify the derivation, without mutating or removing any input row. This
is the only place in the codebase that materializes a continuous pre-2022
Antwerp-Bruges series; without it, `port_id=BE_0BE003` would have no rows before 2022
at all (per the module docstring, L21-24).

**Inputs & Assumptions:**
- `rows` (`list[PortThroughputRow]`): all reported (non-derived) throughput rows for
  both datasets, already unit-converted and code-mapped. Trust: **semi-trusted** — each
  row already passed through `pydantic` validation in `PortThroughputRow` (models.py
  L55-63) and was constructed by `build_direction_rows`/`build_cargo_rows`
  (throughput.py), but the *set* of rows (completeness, absence of duplicates for the
  same natural key) is not verified by this function.
- Implicit: `LEGACY_PORT_CODES = ("BE_0BEANR", "BE_0BEZEE")` (L39),
  `MERGED_PORT_CODE = "BE_0BE003"` (L38), `ANTWERP_BRUGES_MERGER_YEAR = 2022`
  (reference_data.py L57). These constants are not cross-checked against
  `reference_data.PORTS` at runtime — see Open Questions.
- Precondition (implicit, relied on by L61-62): for any key present in
  `values_by_key`, its value dict's keys are a non-empty subset of
  `LEGACY_PORT_CODES` of size 1 or 2. Established by construction at L48-53 — a key is
  only ever inserted via `setdefault` when a qualifying row is seen (L50 filters
  `row.port_code not in LEGACY_PORT_CODES`), so a key with zero entries can never exist
  in the dict. This is what makes the `len(values) < 2` branch equivalent to
  "exactly one of the two legacy ports reported" rather than "zero, one, or two."
- Precondition (unenforced): input `rows` contains at most one row per
  `(port_code, cargo_type_code, direction, year)` natural key. Nothing in this function
  checks it — see Block-by-Block L48-53 and Cross-Function Dependencies.

**Outputs & Effects:**
- Returns `(derived_rows, flags)`. `derived_rows` are new `PortThroughputRow` objects
  with `port_code=MERGED_PORT_CODE`, `source=DERIVED_SOURCE` (L84-92); `flags` is
  `_merger_summary_flags(...)` output followed by per-combination
  `MISSING_YEAR` flags (L94).
- No mutation of the input `rows` list or its elements — the function only reads
  `row.port_code`, `row.year`, `row.cargo_type_code`, `row.direction`,
  `row.gross_weight_tonnes` (L50-53) and never assigns back into a `PortThroughputRow`.
  This upholds the docstring's claim at L46-47.
- No I/O, no external calls, pure function of its argument.

**Block-by-Block:**

```python
# L48-53
values_by_key: dict[tuple[str, str, int], dict[str, float]] = {}
for row in rows:
    if row.port_code not in LEGACY_PORT_CODES or row.year >= ANTWERP_BRUGES_MERGER_YEAR:
        continue
    key = (row.cargo_type_code, row.direction.value, row.year)
    values_by_key.setdefault(key, {})[row.port_code] = row.gross_weight_tonnes
```
- **What:** Filters `rows` down to pre-2022 legacy-port rows and buckets each
  `(cargo_type_code, direction, year)` combination's per-port values.
- **Why here:** Establishes the candidate set before any summing decision is made;
  the merger-year cutoff is enforced here rather than trusted from upstream
  eligibility logic (`throughput.py::_eligible_years` computes a *different* notion of
  eligibility used only for gap-flagging, not for row admission — see Cross-Function
  Dependencies).
- **Assumes:** each `(port_code, cargo_type_code, direction, year)` combination appears
  at most once across `rows`. If it appears twice, the second occurrence silently
  overwrites the first at `values_by_key.setdefault(key, {})[row.port_code] = ...`
  (last-write-wins, no warning, no flag). Nothing in this function detects or reports a
  collision.
- **Establishes:** for every key in `values_by_key`, the value dict's keys are a subset
  of `LEGACY_PORT_CODES` with size ≥ 1 (never 0, since a key is only created on a
  qualifying row).
- **Depended on by:** L61-62 (the "exactly one missing" branch) and L83-92 (the sum).

```python
# L58-81
for (cargo_type_code, direction_value, year), values in sorted(
    values_by_key.items(), key=lambda kv: kv[0]
):
    if len(values) < len(LEGACY_PORT_CODES):
        missing_port = next(p for p in LEGACY_PORT_CODES if p not in values)
        incomplete_flags.append(
            DataQualityFlag(
                flag_type=FlagType.MISSING_YEAR,
                port_code=MERGED_PORT_CODE,
                throughput_ref=ThroughputRef(
                    port_code=MERGED_PORT_CODE,
                    ...
                ),
                ...
            )
        )
        continue
```
- **What:** For any combo where only one of the two legacy ports reported, records a
  `MISSING_YEAR` flag and skips row derivation for that combo.
- **Why here:** Runs before the summation branch so an incomplete combo can never reach
  the `sum()` at L89 — the `continue` at L81 is the guard.
- **Assumes:** `len(values) < 2` implies exactly one port is missing (not zero) — true
  only because of the invariant established at L48-53 (values dict is never empty for
  an existing key). `next(...)` would silently return the *first* `LEGACY_PORT_CODES`
  entry not present in `values` even if both were absent; that case cannot occur given
  the established invariant, but the `next()` call itself has no fallback/default and
  would raise `StopIteration` if the invariant were ever violated (e.g., if
  `LEGACY_PORT_CODES` grew to include a code that could also be fully absent from a
  key that still somehow got created).
- **Establishes:** the flag's `throughput_ref.port_code` and `port_code` fields are set
  to `MERGED_PORT_CODE`, **not** to the port that actually failed to report or to the
  port that did report. A consumer filtering flags by the real legacy port code
  (`BE_0BEANR`/`BE_0BEZEE`) will not see this flag; it is only visible when filtering
  by `BE_0BE003`, a port code that structurally has no rows for this
  `(cargo, direction, year)` at all (pre-merger, by definition).
- **Depended on by:** downstream data-quality reporting/consumers of the
  `data_quality_flags` table (not visible here — see Cross-Function Dependencies).

```python
# L83-92
derived_rows.append(
    PortThroughputRow(
        port_code=MERGED_PORT_CODE,
        cargo_type_code=cargo_type_code,
        year=year,
        direction=Direction(direction_value),
        gross_weight_tonnes=sum(values.values()),
        source=DERIVED_SOURCE,
    )
)
```
- **What:** Builds the derived summed row for a combo where both legacy ports
  reported.
- **Why here:** Reached only via the `else` of the L61 check (by falling through the
  `continue`), so `len(values) == 2` is guaranteed at this point (values' keys are a
  subset of a 2-element tuple and the `<2` branch already handled size 1).
- **Assumes:** `Direction(direction_value)` succeeds — true because `direction_value`
  was produced from `row.direction.value` at L52, i.e., it is always one of the values
  already enumerated by `Direction` (models.py L13-16); the round-trip cannot fail.
  Also assumes `gross_weight_tonnes` values from the two legacy ports are
  commensurable (same unit, no double-counting of intra-complex traffic) — this
  assumption is *not* checked anywhere in code; it is asserted only in prose, in the
  flag description built by `_merger_summary_flags` (L131-137) and in
  `docs/data-quality-notes.md` (referenced, not read here).
- **Establishes:** one `PortThroughputRow` per complete combo, source-tagged as
  derived so it is distinguishable from Eurostat-reported rows downstream.
- **Depended on by:** `pipeline.py::transform_all` (L22-24), which concatenates this
  into the full `rows` list that reaches the loader.

```python
# L94
flags = _merger_summary_flags(has_derived_rows=bool(derived_rows)) + incomplete_flags
return derived_rows, flags
```
- **What:** Combines the fixed summary/explanatory flags with the per-combo
  incompleteness flags and returns everything.
- **Why here:** `has_derived_rows` must be known before calling `_merger_summary_flags`,
  which only exists after the main loop completes.
- **Assumes:** `_merger_summary_flags` correctly reflects whether any derived rows
  exist (see that function's analysis below).
- **Establishes:** the returned `flags` list always contains the two unconditional
  `PORT_MERGER` flags for `BE_0BEANR`/`BE_0BEZEE` (L99-118 of `_merger_summary_flags`)
  regardless of whether `rows` contained any legacy-port data at all.

**Cross-Function Dependencies:**
- Callee `_merger_summary_flags` (internal, L98-154): read in full — see its own
  analysis below. This function depends on it only for the fixed/explanatory
  `PORT_MERGER` flags; it does not depend on it for any row-derivation logic.
- Callee `Direction(...)` / `Direction.value` (external — stdlib `enum.StrEnum` via
  `port_analytics.models`): depended on for a lossless round trip; see assumption
  above. No failure path exists given how `direction_value` is produced.
- Caller `pipeline.py::transform_all` (L14-26): calls this with
  `reported_rows = direction_rows + cargo_rows` (L21-22) — the concatenation of
  `build_direction_rows` and `build_cargo_rows` output. `transform_all` assumes this
  function does not mutate `reported_rows` (true, per above) and that the derived rows
  it returns are disjoint in natural key from `reported_rows` (true structurally: all
  derived rows have `port_code=MERGED_PORT_CODE` and `year < ANTWERP_BRUGES_MERGER_YEAR`,
  a combination `build_direction_rows`/`build_cargo_rows` cannot produce, since those
  only emit rows for `port_code`s that appear in a Eurostat payload under their
  as-reported code, and BE_0BE003 does not report before 2022 per the module's own
  premise, L3-5). This disjointness is **not enforced by any code**; it follows only
  from the real-world absence of pre-2022 BE_0BE003 data in the Eurostat source. If a
  future payload ever contained a `rep_mar=BE_0BE003` observation for a year before
  2022, `build_direction_rows`/`build_cargo_rows` would happily emit it (they filter
  only on `port_code not in PORTS`, throughput.py L52/L109 — `BE_0BE003` is in `PORTS`,
  reference_data.py L11), and `transform_all` would concatenate it with this function's
  derived row for the same key with no collision check anywhere in `transform_all` or
  here.
- Upstream producers `build_direction_rows` / `build_cargo_rows` (throughput.py L37-139,
  internal, read in full): this function's "at most one row per natural key" assumption
  (see L48-53 above) is *not* explicitly established by either producer. Each iterates
  `decode_observations(payload)` once and appends a row per observation with no
  dedup/collision check against rows already appended in the same call. Uniqueness
  instead rests on `decode_observations` (jsonstat.py L23-50): each output row
  corresponds to a distinct flat-index key in the JSON-stat `value` map (JSON object
  keys are unique), and the stride arithmetic (L35-45) deterministically maps distinct
  flat indices to distinct `dimension_values` combinations *provided* `sizes`,
  `dimension_ids`, and the flat-index range in `payload["value"]` are mutually
  consistent with the declared dimension `category.index` maps — a payload-shape
  assumption `decode_observations` does not itself verify (no bounds check on
  `position` against `sizes[i]` at L44). A malformed but structurally-parseable payload
  (e.g., `size` not matching `dimension` category counts) could in principle make two
  distinct flat indices decode to the same `dimension_values`, which would then
  silently collide inside this function's `values_by_key` at L53.
- Shared state: none (pure function, no module-level mutable state touched here beyond
  reading the module-level constants).
- Invariant coupling: the module's stated invariant "raw per-legacy-port rows are
  always kept, untouched" (L9-10 module docstring) is upheld by this function never
  writing into `rows`; the invariant "every derived row is backed by a
  `data_quality_flags` row stating it's computed" (L16-19) is upheld only in aggregate
  — the per-combo flags at L63-80 exist for *incomplete* combos (which get no row), and
  the blanket `BE_0BE003` explanatory flag from `_merger_summary_flags` covers *all*
  derived rows collectively (L120-152 of that function), not one flag per derived row.

**Open Questions:**
- unclear; need to inspect whether any test or runtime check cross-validates
  `LEGACY_PORT_CODES`/`MERGED_PORT_CODE` (continuity.py L38-39) against
  `reference_data.PORTS`'s `merged_into` links (reference_data.py L12-23) — currently
  these are two independently-maintained sources of truth for the same fact.
- unclear; need to inspect whether `pipeline.py::transform_all` or any downstream
  loader step de-duplicates/validates natural-key uniqueness across
  `reported_rows + derived_rows` before writing to `port_throughput` — if not, the
  "disjoint key space" property this function's correctness leans on is enforced
  nowhere in code, only by the real-world data shape.
- unclear; need to inspect `decode_observations` callers' payload validation (if any)
  upstream of `throughput.py`, to know whether `sizes`/`dimension` mismatches
  (jsonstat.py L44, no bounds check) are excluded by an earlier schema-validation step
  not visible in this file.

---

## `_merger_summary_flags` in src/port_analytics/transform/continuity.py (L98-154)

**Purpose:** Produces the fixed, human-readable `data_quality_flags` rows that
document the Antwerp-Bruges merger for downstream consumers: two unconditional flags
noting that Antwerpen and Zeebrugge report separately pre-2022, and — only when the
caller reports derived rows exist — a third flag on `BE_0BE003` spelling out exactly
why the derived summed series is not an authoritative Eurostat figure and what its
known weaknesses are (L120-152). Without this function, the derived rows produced by
`derive_pre_merger_antwerp_bruges` would carry no explanation of their provenance
beyond the `source='derived_sum:...'` string tag.

**Inputs & Assumptions:**
- `has_derived_rows` (bool, keyword-only, L98): caller-supplied flag controlling
  whether the third explanatory flag is included. Trust: trusted — the only caller
  (`derive_pre_merger_antwerp_bruges` L94) computes it as `bool(derived_rows)` directly
  from its own local state, so this parameter is always accurate at the call site.
- Implicit: `MERGED_PORT_CODE` and `DERIVED_SOURCE` module constants (L38, L40), used
  only for string interpolation into flag descriptions/port_code — not validated
  against anything.
- No precondition on `rows` or any other external state — this function takes no other
  input and reads no module-level mutable state.

**Outputs & Effects:**
- Returns a `list[DataQualityFlag]` of length 2 or 3, always freshly constructed (no
  caching/memoization) — every call rebuilds the same literal text.
- No mutation, no I/O.

**Block-by-Block:**

```python
# L99-118
flags = [
    DataQualityFlag(flag_type=FlagType.PORT_MERGER, port_code="BE_0BEANR", ...),
    DataQualityFlag(flag_type=FlagType.PORT_MERGER, port_code="BE_0BEZEE", ...),
]
```
- **What:** Two unconditional flags stating that Antwerpen/Zeebrugge report separately
  through 2021 and are linked via `merged_into_port_id`.
- **Why here:** Independent of whether any derivation succeeded — these describe the
  *raw* data's shape (the legacy ports exist and merged), not the derived series, so
  they are always emitted by design.
- **Assumes:** `"BE_0BEANR"` and `"BE_0BEZEE"` are valid, FK-referenceable port codes
  at load time. Established in `reference_data.PORTS` (L12-23), not re-verified here —
  this function hardcodes the literals independently of `LEGACY_PORT_CODES`
  (L39 of this file) rather than reusing the constant.
- **Establishes:** these two flags are present in every call's output regardless of
  `has_derived_rows` or whether any legacy-port row was ever present in the original
  `rows` passed to the caller — i.e., they are emitted even for a dataset that has no
  BE_0BEANR/BE_0BEZEE rows at all.
- **Depended on by:** the caller's combined `flags` list (L94), and ultimately whatever
  loads `data_quality_flags` into the target schema.

```python
# L120-152
if has_derived_rows:
    flags.append(
        DataQualityFlag(
            flag_type=FlagType.PORT_MERGER,
            port_code=MERGED_PORT_CODE,
            description=(... long explanation of summation risks ...),
            resolution=(... points consumers to merged_into_port_id vs port_id=BE_0BE003 ...),
        )
    )
return flags
```
- **What:** Conditionally appends the explanatory flag for the derived series.
- **Why here:** Gated on `has_derived_rows` so this flag is never emitted for a
  dataset/run that produced zero derived rows (e.g., if every combo was incomplete, or
  no legacy-port pre-2022 data existed at all) — avoids a dangling flag on a port code
  (`BE_0BE003`) with no matching derived rows in that scenario.
- **Assumes:** `has_derived_rows` accurately reflects the caller's actual output (see
  Inputs above) — this function has no independent way to verify it.
- **Establishes:** a 1:many relationship — one flag covers *all* derived rows from a
  given call, not one flag per row. A consumer expecting a `data_quality_flags` row per
  `(cargo_type, direction, year)` derived combination (by analogy with the per-combo
  `MISSING_YEAR` flags the caller also produces for incomplete combos) will not find
  one; only the aggregate note exists.
- **Depended on by:** nothing further within this module; this is the terminal
  construction before return.

**Cross-Function Dependencies:**
- Callers: only `derive_pre_merger_antwerp_bruges` (L94, same file), which trusts this
  function to (a) always include the two legacy-port flags and (b) include the third
  flag if and only if `has_derived_rows` is true. Both are honored per the
  block-by-block above.
- Shared state: none.
- Invariant coupling: the module docstring's claim that "every derived row is backed by
  a port_merger data_quality_flags row" (L16-17) is satisfied only in the aggregate,
  one-flag-covers-many-rows sense described above, not per-row.

**Open Questions:**
- unclear; need to inspect whether any downstream consumer or test asserts a per-row
  (rather than per-batch) relationship between derived rows and `PORT_MERGER` flags —
  if so, the aggregate-flag design here would not satisfy it.
- unclear; need to inspect why this function hardcodes `"BE_0BEANR"`/`"BE_0BEZEE"`
  string literals (L102, L111) instead of indexing `LEGACY_PORT_CODES` (L39) — whether
  this duplication is intentional (e.g., for readability) or a maintenance hazard if
  `LEGACY_PORT_CODES` ever changes.
