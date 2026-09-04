# Function analyses: transform/jsonstat.py and transform/pipeline.py

---

## `decode_observations` in src/port_analytics/transform/jsonstat.py (L23-L50)

**Purpose:** Decodes a JSON-stat 2.0 payload's sparse, flat-indexed `value` map into a
list of `JsonStatObservation`s, each carrying the actual category code (not a numeric
index) for every dimension. It is the single point where flat integer keys are turned
back into semantically meaningful `dimension_values` — every downstream consumer
(`throughput.py`'s `build_direction_rows`/`build_cargo_rows`) works only with the
decoded dimension-code dicts, never with the raw payload's indices. Without it, callers
would have to re-derive dimension order/stride arithmetic themselves.

**Inputs & Assumptions:**
- `payload` (dict[str, Any]): a full JSON-stat 2.0 document, ultimately sourced from the
  Eurostat API via the ingest layer and passed through `json.loads` with no schema
  validation (see `cli.py` L31-37, which does a bare `json.loads` before calling
  `transform_all` → `build_direction_rows`/`build_cargo_rows` → this function). Trust:
  **untrusted/adversarial** — nothing between the HTTP response and this function
  validates shape, types, or ranges.
- Implicit: no state, no I/O, no clock. Pure function of `payload`.
- Preconditions assumed but **not checked here**:
  - `payload["id"]`, `payload["size"]`, `payload["dimension"]`, `payload["value"]` all
    exist. Nothing establishes this; a missing key raises `KeyError` (L24-27),
    uncaught, propagating to the caller.
  - `len(payload["id"]) == len(payload["size"])`. This is only enforced indirectly, and
    only when `value_map` is non-empty: `zip(dimension_ids, strides, strict=True)` at
    L43 raises `ValueError` on mismatch, but that line only executes inside the
    `for flat_index_str, value in value_map.items()` loop (L40). If `payload["value"]`
    is empty, the mismatch is silently never detected and the function returns `[]`.
  - For every `dim_id` in `dimension_ids`, `dimensions[dim_id]["category"]["index"]`
    exists and is a `dict[str, int]` (L31). A missing `dim_id` key, missing
    `"category"`, or missing `"index"` raises `KeyError`, uncaught.
  - The values in each `index_map` (the per-category position ints) are a 0-based,
    contiguous permutation `0..n-1` matching that dimension's `sizes[i]`, per the
    JSON-stat spec. **This is never verified.** `codes_by_dimension[dim_id]` is built by
    sorting `index_map.items()` by the position value and taking the codes in that
    order (L32-33) — the resulting list's *index* is trusted to equal the *position*
    used in flat-index arithmetic. If the index values are non-contiguous, gapped,
    duplicated, or don't match `sizes[i]` in count, the list-index-vs-declared-position
    correspondence silently breaks: a computed `position` derived from `strides` (which
    come only from `payload["size"]`, independent of `index_map`) may point at the wrong
    code, or past the end of the list.
  - Each `flat_index_str` key in `payload["value"]` is a base-10 integer string encoding
    a valid flat offset into the row-major space defined by `sizes` (`int(flat_index_str)`
    at L41 — raises `ValueError` if not parseable as an int; no range check against the
    product of `sizes` at all).
  - Each `value` in `payload["value"]` is coercible to `float` (L47, bare `float(value)`,
    uncaught `ValueError`/`TypeError` on failure).

**Outputs & Effects:**
- Returns `list[JsonStatObservation]`, one per key present in `payload["value"]`
  (missing flat indices are absent data, per the module docstring L7-8 — no observation
  is synthesized for them).
- No mutation of `payload`. No I/O. No exceptions are caught or translated — every
  failure mode above surfaces as a raw `KeyError`/`ValueError`/`IndexError`/pydantic
  `ValidationError` to the caller.

**Block-by-Block:**

```python
# L24-27
dimension_ids: list[str] = payload["id"]
sizes: list[int] = payload["size"]
dimensions: dict[str, Any] = payload["dimension"]
value_map: dict[str, float] = payload["value"]
```
- **What:** Pulls the four top-level fields the rest of the function needs.
- **Why here:** Fail-fast positioning — if these keys are absent the function dies
  immediately rather than partway through decoding.
- **Assumes:** All four keys are present and have the annotated shapes; the annotations
  are not runtime-checked (plain dict subscripting).
- **Establishes:** Nothing — no validation, just extraction.
- **Depended on by:** L30-37 depend on `dimension_ids`/`sizes`/`dimensions`; L40-48
  depend on `value_map`.

```python
# L29-33
codes_by_dimension: dict[str, list[str]] = {}
for dim_id in dimension_ids:
    index_map: dict[str, int] = dimensions[dim_id]["category"]["index"]
    ordered = sorted(index_map.items(), key=lambda item: item[1])
    codes_by_dimension[dim_id] = [code for code, _position in ordered]
```
- **What:** For each dimension, builds an ordered list of category codes, ordered by
  the dimension's declared position index.
- **Why here:** Precomputes the code lookup tables once, outside the per-observation
  loop, rather than re-sorting per observation.
- **Assumes:** `dimensions[dim_id]["category"]["index"]` exists (else `KeyError`); its
  values form a dense 0-based permutation whose length equals `sizes[dimension_ids.index(dim_id)]`
  — **unenforced**, nothing checks `len(index_map) == sizes[i]` or that the position
  values are exactly `{0, ..., len(index_map)-1}`.
- **Establishes:** A `dim_id -> [code, ...]` map whose correctness (index i holds the
  code for position i) rests entirely on the unverified assumption above.
- **Depended on by:** L45 (`codes_by_dimension[dim_id][position]`), which will raise
  `IndexError` if `position` (computed independently from `sizes`/strides) exceeds
  `len(codes_by_dimension[dim_id]) - 1`, or silently return the wrong code if positions
  are non-contiguous but happen to stay in range.

```python
# L35-37
strides = [1] * len(sizes)
for i in range(len(sizes) - 2, -1, -1):
    strides[i] = strides[i + 1] * sizes[i + 1]
```
- **What:** Standard row-major stride computation from `sizes`, independent of
  `codes_by_dimension` and of `dimension_ids` content.
- **Why here:** Needed before the decode loop so each flat index can be split into
  per-dimension positions via successive `//`/`%`.
- **Assumes:** `sizes` entries are non-negative integers reflecting true cardinality per
  dimension, matching the corresponding `index_map` length (see above) — unenforced.
  `sizes` could contain zero, causing all subsequent strides for earlier dimensions to
  be `0`, which would make `remainder // 0` raise `ZeroDivisionError` in the loop below
  for any dimension after a zero-sized one.
- **Establishes:** `strides[i]` for use in L44.
- **Depended on by:** L44 (division/modulo to extract `position`).

```python
# L40-48
for flat_index_str, value in value_map.items():
    remainder = int(flat_index_str)
    dimension_values: dict[str, str] = {}
    for dim_id, stride in zip(dimension_ids, strides, strict=True):
        position, remainder = remainder // stride, remainder % stride
        dimension_values[dim_id] = codes_by_dimension[dim_id][position]
    observations.append(JsonStatObservation(dimension_values=dimension_values, value=float(value)))
```
- **What:** For every present flat index, decodes it into a per-dimension code dict and
  builds one `JsonStatObservation`.
- **Why here:** The core decode step; runs once per sparse entry, so cost is
  proportional to reported observations, not the full dense cartesian space.
- **Assumes:** (a) `flat_index_str` parses as `int` (L41); (b) `len(dimension_ids) ==
  len(strides)`, enforced here via `strict=True` but *only reached if `value_map` is
  non-empty*; (c) `0 <= position < len(codes_by_dimension[dim_id])` for every
  dimension on every iteration — **not checked**; a flat index at or beyond the product
  of `sizes` (or a negative one) is not rejected. For a negative `flat_index_str`,
  Python's floor-division/modulo semantics (`//` floors, `%` returns a non-negative
  result for positive `stride`) mean the arithmetic doesn't raise but instead maps the
  value to an in-range but semantically wrong position/dimension combination — a silent
  misattribution rather than a crash; (d) `value` is float-coercible (L47).
- **Establishes:** The final `observations` list returned to the caller. Each
  `JsonStatObservation` is pydantic-validated at construction (L46-48) — `dimension_values`
  must be `dict[str, str]` (true by construction, since codes and `dim_id`s are always
  strings) and `value` must be `float` (already coerced by the explicit `float(value)`
  call, so pydantic validation here is close to a no-op given the value's already the
  right type).
- **Depended on by:** Every caller — `build_direction_rows` and `build_cargo_rows` in
  `throughput.py` — which read `obs.dimension_values` by key (`dv.get("unit")`,
  `dv.get("rep_mar")`, `dv.get("direct")`/`dv.get("cargo")`, and `dv["time"]` accessed
  *without* `.get()` at `throughput.py` L54/L111 — a `KeyError` there if `"time"` is
  absent from `payload["id"]`, which this function does not guard against) and
  `obs.value` as a trusted numeric gross-weight figure.

**Cross-Function Dependencies:**
- Callee: none — this function makes no calls into other project code (only stdlib
  `sorted`/`zip`/`int`/`float` and pydantic's `BaseModel.__init__`).
- Callers: `build_direction_rows` and `build_cargo_rows` in
  `src/port_analytics/transform/throughput.py` (L46, L103) call `decode_observations(payload)`
  with no `try`/`except` around it — any exception raised here (`KeyError`, `ValueError`,
  `IndexError`, `ZeroDivisionError`, pydantic `ValidationError`) propagates unmodified
  through those functions, then through `transform_all` (`pipeline.py`), then to
  `cli.py`'s `run()` (L40), which also has no surrounding `try`/`except` — an
  unhandled exception there terminates the CLI. Both callers trust that every
  `JsonStatObservation.dimension_values` entry, when present, holds a code drawn from
  the intended domain vocabulary — they re-validate this themselves via allowlist
  membership checks (`port_code not in PORTS`, `direct_code not in DIRECTION_CODES`,
  `cargo_code not in cargo_codes`, `dv.get("unit") != "THS_T"`) rather than trusting
  `decode_observations` to have filtered anything. They do **not** re-validate that
  `dv["time"]` is present or numeric.
- Shared state: none — pure function, no module-level mutable state.
- Invariant couplings: the correctness of every downstream `PortThroughputRow.year`,
  `.port_code`, `.direction`, `.cargo_type_code` depends transitively on the position/
  code alignment this function assumes but does not verify (see L29-33, L40-48 above).

**Open Questions:**
- Is there schema validation of the Eurostat response anywhere in the ingest layer
  (`port_analytics.ingest.pull`) before it reaches `cli.py`'s `json.loads` and eventually
  this function? Not inspected in this pass — if none exists, every unenforced
  assumption listed above is reachable from raw network input.
- What does pydantic v2's `dict[str, str]` field validation do if a code in
  `codes_by_dimension` were somehow a non-string (shouldn't happen given `index_map`
  keys are JSON object keys, hence always strings after `json.loads`, but not
  independently confirmed for exotic payloads) — unclear; not exercised by this reading.
- Does anything upstream guarantee `sizes[i] == len(dimensions[dimension_ids[i]]["category"]["index"])`
  for genuine Eurostat responses (i.e., is the unenforced assumption in practice always
  true for this API), or is it purely a spec expectation with no server-side guarantee
  from the caller's point of view? Not established from this function's code alone.

---

## `transform_all` in src/port_analytics/transform/pipeline.py (L14-L26)

**Purpose:** Top-level orchestration of the raw-to-domain transform: decodes both raw
Eurostat payloads into `PortThroughputRow`s via `build_direction_rows`/`build_cargo_rows`,
then layers the Antwerp-Bruges pre-merger continuity derivation on top, and returns the
combined row and flag lists. Per its docstring (L1-3) and `cli.py` (L17, L40), this is
the single function Phase 3's loader/CLI calls to go from landed JSON to loadable rows —
without it, the loader would have to know the per-dataset build functions and the
continuity-derivation ordering itself.

**Inputs & Assumptions:**
- `direction_payload` (dict[str, Any]): raw JSON-stat payload for dataset
  `mar_mg_aa_pwhd` (goods by direction). Trust: **untrusted** — passed straight from
  `cli.py`'s `json.loads` of a landed file (`cli.py` L30-37), no validation before this
  call.
- `cargo_payload` (dict[str, Any]): raw JSON-stat payload for dataset `mar_mg_am_pwhc`
  (goods by cargo type). Same trust level.
- Implicit: no shared state, no clock, no caller identity. Purely a composition of three
  pure(ish) sub-transforms.
- Preconditions: none stated or checked at this level; every precondition belongs to the
  callees (see below). The function itself does not inspect either payload's shape.

**Outputs & Effects:**
- Returns `tuple[list[PortThroughputRow], list[DataQualityFlag]]`: `rows` is the
  concatenation of both datasets' reported rows plus the derived Antwerp-Bruges rows
  (L24); `flags` is the concatenation of both datasets' gap-flags plus the merger flags
  (L25).
- No I/O, no mutation of inputs, no exceptions caught — anything raised by any callee
  propagates unmodified to `transform_all`'s caller.

**Block-by-Block:**

```python
# L18-19
direction_rows, direction_flags = build_direction_rows(direction_payload)
cargo_rows, cargo_flags = build_cargo_rows(cargo_payload)
```
- **What:** Independently decodes each raw payload into domain rows plus per-dataset
  gap-quality flags.
- **Why here:** Both calls are independent of each other (different payloads, no shared
  state) and must both complete before the merger derivation, which needs rows from
  both datasets together.
- **Assumes:** Each call either returns cleanly or raises — there is no partial-success
  path (both `build_direction_rows`/`build_cargo_rows` build their full row/flag lists
  in one pass over `decode_observations`'s output before returning; see `throughput.py`
  L37-82, L85-139). If `build_direction_rows` raises, `build_cargo_rows` never runs and
  no rows/flags from either are returned — this function has no error handling, so a
  malformed `direction_payload` prevents `cargo_payload` from ever being processed even
  though it might have been decodable on its own.
- **Establishes:** `direction_rows`/`cargo_rows`, each row's `port_code`,
  `cargo_type_code`, `direction`, `year` drawn only from the respective dataset's
  allowlisted codes (enforced inside the callees, not here).
- **Depended on by:** L21 (`reported_rows`), which feeds L22.

```python
# L21-22
reported_rows = direction_rows + cargo_rows
derived_rows, merger_flags = derive_pre_merger_antwerp_bruges(reported_rows)
```
- **What:** Concatenates both datasets' reported rows, then derives the pre-2022
  Antwerp-Bruges continuity rows by summing legacy-port rows found in that combined
  list.
- **Why here:** `derive_pre_merger_antwerp_bruges` (see `continuity.py` L43-95) needs
  visibility into *both* legacy ports' rows (`BE_0BEANR` from the direction dataset,
  `BE_0BEZEE` potentially from either) to pair them by `(cargo_type_code,
  direction.value, year)`; it must run after both builds, not per-dataset.
- **Assumes:** `reported_rows` contains, for each legacy port/cargo/direction/year
  combination, at most one row per source (true given `build_direction_rows`/
  `build_cargo_rows` each only emit rows for their own `source` value and dedupe within
  themselves via distinct `(port_code, direct_code)`/`(port_code, cargo_code)`
  presence tracking — `throughput.py` L44-65, L100-122 — though nothing in
  `transform_all` itself checks for duplicate `(cargo_type_code, direction, year)`
  entries across the two lists; `derive_pre_merger_antwerp_bruges` would silently let a
  later duplicate overwrite an earlier one in `values_by_key.setdefault(key,
  {})[row.port_code] = ...`, `continuity.py` L53, since it keys only by
  `row.port_code`, not by `row.source`).
- **Establishes:** `derived_rows` (the summed `BE_0BE003` pre-merger series) and
  `merger_flags` (the accompanying documentation/gap flags).
- **Depended on by:** L24-25.

```python
# L24-26
rows = reported_rows + derived_rows
flags = direction_flags + cargo_flags + merger_flags
return rows, flags
```
- **What:** Final concatenation and return.
- **Why here:** Simple aggregation; order within `rows`/`flags` is reported-then-derived
  and direction-then-cargo-then-merger respectively, but nothing downstream (per the
  loader, not inspected here) is shown to depend on that order.
- **Assumes:** `reported_rows`, `derived_rows`, `direction_flags`, `cargo_flags`,
  `merger_flags` are all well-formed lists of the expected pydantic model type —
  guaranteed by the callees' own return-type construction, not re-checked here.
- **Establishes:** The tuple contract `(list[PortThroughputRow], list[DataQualityFlag])`
  that `cli.py` L40 destructures directly into `rows, flags`.
- **Depended on by:** `cli.py` L44 (row/flag counts echoed to the user) and L48
  (`load_all(conn, rows, flags)` — the loader that persists to Azure SQL; not inspected
  in this pass).

**Cross-Function Dependencies:**
- Callee `build_direction_rows` (internal, `throughput.py` L37-82): `transform_all`
  depends on it to (a) filter `decode_observations`'s output down to `unit == "THS_T"`
  rows with allowlisted `port_code`/`direct_code` (`throughput.py` L48-53), (b) convert
  thousand-tonnes to tonnes (L61), (c) compute gap flags for every port/direction pair
  even where no rows exist (L67-80, driven by `PORTS`/`DIRECTION_CODES`, not by what
  happened to be present in the payload). It does **not** guard against
  `decode_observations` raising (see that function's analysis above) — any such
  exception propagates through unchanged.
- Callee `build_cargo_rows` (internal, `throughput.py` L85-139): symmetric role for the
  cargo-type dataset; additionally excludes `cargo_type_code == "TOTAL"` and `"UNK"` by
  construction of `cargo_codes` (L101), a decision `transform_all` relies on to avoid
  duplicate TOTAL rows between the two datasets (documented at `throughput.py` L91-96)
  but does not itself verify.
- Callee `derive_pre_merger_antwerp_bruges` (internal, `continuity.py` L43-95):
  `transform_all` depends on it to never mutate or remove `reported_rows` entries
  (stated in its docstring, `continuity.py` L46-47, and consistent with its
  implementation, which only reads `rows` and builds new lists) and to only ever emit
  rows keyed under `MERGED_PORT_CODE = "BE_0BE003"` (`continuity.py` L38, L84-92) — so
  concatenating `reported_rows + derived_rows` at L24 cannot double-count a legacy-port
  row as a merged-port row.
- Callers: `cli.py` `run()` (L40-43) is the only caller. It assumes `transform_all`
  either returns two well-formed lists or raises — there is no partial/degraded-result
  contract, and `run()` has no exception handling around the call, so any propagated
  exception (including anything originating deep in `decode_observations`) aborts the
  whole CLI run before `load_all` is reached, i.e., before anything is written to Azure
  SQL.
- Shared state: none between `transform_all` and its callees beyond the data passed by
  value/reference through arguments and return values; no module-level mutable state in
  `pipeline.py`, `throughput.py`, or `continuity.py`.
- Invariant couplings: `transform_all`'s output invariant "every row's
  `(port_code, cargo_type_code, direction, year)` combination that Eurostat could have
  reported is either present as a row or explained by a flag" is composed from three
  independently-maintained sub-invariants (direction-dataset gap coverage, cargo-dataset
  gap coverage, merger-derivation completeness); nothing in `transform_all` cross-checks
  that the three flag sets don't overlap or contradict each other for the same
  `ThroughputRef`.

**Open Questions:**
- What does `load_all` (`load/loader.py`, not read in this pass) assume about row/flag
  ordering or about the absence of duplicate `(port_code, cargo_type_code, direction,
  year)` keys within `rows`? If it assumes uniqueness, the unchecked-duplicate path
  noted at L21-22 above (two sources producing a row for the same natural key) becomes
  relevant to whether that assumption actually holds at the loader boundary.
- Does `pull_and_land` (`ingest/pull.py`, not read in this pass) perform any validation
  of the Eurostat response before writing it to the landing directory, or is the
  `json.loads` in `cli.py` L31 the very first structural touch of the data — i.e., is
  `transform_all`'s untrusted-input boundary identical to the network boundary? Not
  established from the files read in this pass.
