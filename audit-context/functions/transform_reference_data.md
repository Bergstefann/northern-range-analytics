# Analysis: src/port_analytics/transform/reference_data.py

## No functions in this file

`src/port_analytics/transform/reference_data.py` (L1-L58) contains no `def` statements — it is a pure
data module: three module-level dict literals (`PORTS` L10-27, `CARGO_TYPES` L38-46, `DIRECTION_CODES`
L48-52) and one scalar constant (`ANTWERP_BRUGES_MERGER_YEAR` L57). There is nothing to walk block-by-block
in the sense of control flow; the content worth recording is the set of data-shape invariants these
structures are built to satisfy, which ones are actually enforced (by Pydantic field types, at construction
time) versus which are cross-record or cross-module conventions enforced by nothing in this file, and how
every downstream consumer (`transform/throughput.py`, `transform/continuity.py`, `load/loader.py`) leans on
those unenforced conventions. That is treated below as a single "module" analysis, in the same shape as a
function analysis: purpose, inputs/assumptions, effects, block-by-block over the four declarations, and
cross-module dependencies, since those consumers are effectively callers reading this module's return value
(the dicts themselves).

---

## Module `reference_data` in src/port_analytics/transform/reference_data.py (L1-L58)

**Purpose:** Supplies the static domain reference data — the finite set of ports, cargo types, and direction
codes the pipeline knows about, plus the one calendar cutover date needed to reason about the Antwerp-Bruges
merger — that every transform and load function treats as ground truth. Nothing in the transform layer
discovers ports or cargo types dynamically from the Eurostat payload; `throughput.py` filters incoming rows
against these dicts (`port_code not in PORTS`, L52/L109 of throughput.py) rather than accepting whatever
codes the API returns. If this module were wrong or incomplete, rows for real ports would be silently
dropped (the "not in PORTS" branches in `throughput.py` skip rather than error) and merger-year logic in
`continuity.py` would derive incorrect series without any signal that the input assumptions had changed.

**Inputs & Assumptions:**
- No parameters — this is data, evaluated once at import time.
- Implicit: `port_analytics.models.Port`, `CargoType`, `Direction` (imported L8) — Pydantic `BaseModel`
  subclasses (models.py L31-42) that type-check fields (`str`, `str | None`) at construction but perform no
  cross-field or cross-record validation (no custom validators appear in models.py L31-42).
- Precondition (unenforced): every dict key equals the `eurostat_code`/`cargo_type_code` field of its value.
  E.g. `"BE_0BE003": Port(eurostat_code="BE_0BE003", ...)` (L11). Nothing in `Port`, in this module, or
  anywhere else checks key/field agreement — a typo'd key with a correct field value (or vice versa) would
  type-check fine and only surface as a lookup mismatch in a caller far from this file.
- Precondition (unenforced): every non-`None` `Port.merged_into` value names a key that exists elsewhere in
  `PORTS`. `BE_0BEANR.merged_into = "BE_0BE003"` (L16) and `BE_0BEZEE.merged_into = "BE_0BE003"` (L22) both
  target the key at L11. No referential-integrity check exists in this file or in `models.Port` — see
  Cross-Function Dependencies for where this is depended on and where it would fail.

**Outputs & Effects:**
- `PORTS: dict[str, Port]` (L10-27) — six entries; three plain ports (Hamburg, Rotterdam, Gdansk) and one
  merger triple (Antwerp-Bruges, Antwerpen, Zeebrugge).
- `CARGO_TYPES: dict[str, CargoType]` (L38-46) — seven entries, deliberately including a synthetic `"TOTAL"`
  (L39, comment L29-33) and deliberately excluding `"UNK"` (comment L35-37, cross-checked against Finding 2
  in `docs/data-quality-notes.md`).
- `DIRECTION_CODES: dict[str, Direction]` (L48-52) — maps raw Eurostat direction codes (`TOTAL`, `IN`, `OUT`)
  onto the `Direction` enum (models.py L13-16).
- `ANTWERP_BRUGES_MERGER_YEAR = 2022` (L57) — a bare `int`, not tied programmatically to which ports in
  `PORTS` it applies to.
- No writes, no I/O, no exceptions raised beyond whatever Pydantic raises at import time if a literal here
  violated a field's declared type (none do, on inspection).
- All three dicts and the constant are mutable/rebindable module globals — nothing freezes them (no
  `MappingProxyType`, no `frozen=True` on the models). Any importer holding a reference to `PORTS` can mutate
  it in place and that mutation is visible to every other module that later does
  `from ... import PORTS` or already holds the same object, since Python caches the module.

**Block-by-Block:**

```python
# L10-27
PORTS: dict[str, Port] = {
    "BE_0BE003": Port(eurostat_code="BE_0BE003", port_name="Antwerp-Bruges", country_code="BE"),
    "BE_0BEANR": Port(..., merged_into="BE_0BE003"),
    "BE_0BEZEE": Port(..., merged_into="BE_0BE003"),
    "DE_1DEHAM": Port(...),
    "NL_0NLRTM": Port(...),
    "PL_0PLGDN": Port(...),
}
```
- **What:** Declares the fixed port universe and encodes the Antwerp-Bruges merger as two `merged_into`
  pointers into the same dict.
- **Why here:** Single source of truth consumed by both transform functions (filtering/eligibility) and the
  loader (FK resolution and the `merged_into_port_id` link).
- **Assumes:** the key/field-agreement and `merged_into` referential-integrity preconditions above; also
  assumes exactly one merger relationship exists in the whole table — see `_eligible_years` dependency below.
- **Establishes:** the closed set of valid `port_code` values used everywhere downstream as a membership
  filter (`in PORTS` / `PORTS[code]`).
- **Depended on by:** `throughput.py` L52, L68-69, L109, L125-126, L152-157, L181; `continuity.py` (indirectly,
  via hardcoded `MERGED_PORT_CODE`/`LEGACY_PORT_CODES` — see below); `loader.py` L49-61, L102, L132.

```python
# L38-46
CARGO_TYPES: dict[str, CargoType] = {
    "TOTAL": CargoType(cargo_type_code="TOTAL", cargo_type_name="Total (all cargo)"),
    "LBK": ..., "DBK": ..., "LCNT": ..., "RO_MSP": ..., "RO_MNSP": ..., "OTH": ...,
}
```
- **What:** Declares the fixed cargo-type universe, including a cargo type (`TOTAL`) that does not correspond
  to any Eurostat `cargo` dimension value.
- **Why here:** `throughput.py` `build_cargo_rows` explicitly excludes `"TOTAL"` from the codes it accepts
  from the cargo dataset (`[code for code in CARGO_TYPES if code != "TOTAL"]`, throughput.py L101) — this
  dict is the source of both the accepted codes and the one exclusion.
  `"UNK"` is absent from this literal entirely (matches the comment at L35-37); `build_cargo_rows`'s
  membership check (`cargo_code not in cargo_codes`, throughput.py L109) therefore drops `UNK` rows as a
  side effect of `UNK` never having been added here, not via an explicit exclusion list.
- **Assumes:** the same key/field-agreement precondition as `PORTS`.
- **Establishes:** the closed set of valid `cargo_type_code` values, and specifically that `"TOTAL"` is a
  member (needed by `build_direction_rows`, which hardcodes `cargo_type_code="TOTAL"` at throughput.py L58
  without looking it up in this dict at all — see Open Questions).
- **Depended on by:** throughput.py L101, L109, L127, L182; loader.py L67-73, L103.

```python
# L48-52
DIRECTION_CODES: dict[str, Direction] = {
    "TOTAL": Direction.TOTAL,
    "IN": Direction.INBOUND,
    "OUT": Direction.OUTBOUND,
}
```
- **What:** Translates raw Eurostat `direct` dimension codes to the domain `Direction` enum.
- **Why here:** `build_direction_rows` uses this both as a membership filter (`direct_code not in
  DIRECTION_CODES`, throughput.py L52) and as the translation table (`DIRECTION_CODES[direct_code]`, L60),
  and iterates it a second time to generate gap flags per direction (L70), including the `"TOTAL"` direction.
- **Assumes:** every raw code Eurostat actually emits for the `direct` dimension is one of these three; any
  other code is silently dropped by the `not in` check rather than flagged.
- **Establishes:** the closed set of valid raw direction codes and their mapping to `Direction`.
- **Depended on by:** throughput.py L52, L60, L70.

```python
# L57
ANTWERP_BRUGES_MERGER_YEAR = 2022
```
- **What:** The year the merged entity begins reporting; legacy codes are assumed to report only through the
  year before, with no overlap (comment L54-56).
- **Why here:** Consumed as a plain cutover boundary by two different modules for two different purposes.
- **Assumes:** exactly one merger event exists across all of `PORTS`, sharing this single year. There is
  currently only one (`BE_0BEANR`/`BE_0BEZEE` → `BE_0BE003`), so the assumption holds by construction, but
  nothing ties this constant to a specific `merged_into` relationship — see Cross-Function Dependencies.
- **Establishes:** the year boundary used both to classify "eligible" years per port
  (`throughput.py::_eligible_years`, L147-157) and to select which rows are summed for the derived series
  (`continuity.py::derive_pre_merger_antwerp_bruges`, L50).
- **Depended on by:** throughput.py L154, L156; continuity.py L50.

**Cross-Function Dependencies:**

- **Caller `throughput.py::build_direction_rows` / `build_cargo_rows` (internal, throughput.py L37-139):**
  filters incoming Eurostat rows by `port_code not in PORTS` / `cargo_code not in cargo_codes` /
  `direct_code not in DIRECTION_CODES`. This function depends on `reference_data` to establish the complete
  and correct set of valid codes; a port or cargo type missing from these dicts is not an error anywhere in
  the pipeline — it is silently excluded from both the row list and the gap-flag accounting (the `continue`
  at throughput.py L49/L106 and the `not in` skip at L52-53/L109-110 happen before any row or flag is
  produced for that code). `reference_data.py` establishes nothing that would surface such an omission.
- **Caller `throughput.py::_eligible_years` (internal, throughput.py L147-157):** reads `PORTS[port_code]`
  (L152) and depends on `reference_data` for two things this function does not itself verify: (1) that
  `port.merged_into is not None` (L153) correctly identifies every legacy port, and (2) that
  `any(p.merged_into == port_code for p in PORTS.values())` (L155) correctly identifies the single merge
  *target*. Both branches apply the one module-level `ANTWERP_BRUGES_MERGER_YEAR` (L57) as the cutover for
  *whichever* merger relationship they find. If `PORTS` ever encoded a second, unrelated merger with a
  different real-world cutover year, `_eligible_years` would apply the Antwerp-specific year to it — nothing
  in `reference_data.py` scopes the constant to the Antwerp/Antwerpen/Zeebrugge triple specifically; the
  scoping is purely the fact that today only one merger exists in the data.
- **Caller `continuity.py::derive_pre_merger_antwerp_bruges` (internal, continuity.py L43-96):** does **not**
  import `PORTS` at all. It hardcodes `MERGED_PORT_CODE = "BE_0BE003"` and
  `LEGACY_PORT_CODES = ("BE_0BEANR", "BE_0BEZEE")` (continuity.py L38-39) as constants independent of
  `reference_data.PORTS`, and imports only `ANTWERP_BRUGES_MERGER_YEAR` (continuity.py L36) to decide which
  rows predate the merger (L50). This function's correctness therefore depends on these two hardcoded string
  triples staying in sync with the `merged_into` relationships declared in `PORTS` (L16, L22) — a dependency
  `reference_data.py` cannot enforce because `continuity.py` never reads the field it would need to check
  against (`Port.merged_into`). Nothing in either file asserts the two representations agree.
- **Caller `load/loader.py::upsert_ports` (internal, loader.py L46-61):** iterates `PORTS.values()` twice.
  The first pass (L49-53) builds `port_ids` keyed by `port.eurostat_code`. The second pass (L55-58) does
  `port_ids[port.merged_into]` for every port with a non-`None` `merged_into`. This is the one place the
  "every `merged_into` value names a key that exists in `PORTS`" precondition is load-bearing at runtime:
  `port_ids` was populated from every `eurostat_code` in `PORTS` in the first pass, so the second-pass lookup
  succeeds if and only if the key/field-agreement and referential-integrity preconditions above both hold.
  Neither is checked before this point; a mismatch surfaces only as a `KeyError` here, not as a data-shape
  error attributable to `reference_data.py` itself.
- **Caller `load/loader.py::upsert_throughput_rows` / `upsert_flags` (internal, loader.py L76-146):** look up
  `PORTS[row.port_code]` / `PORTS[flag.port_code]` (L102, L132) and `CARGO_TYPES[row.cargo_type_code]` (L103)
  assuming every `port_code`/`cargo_type_code` that reaches this point already passed the `PORTS`/`CARGO_TYPES`
  membership filters upstream in `throughput.py` and `continuity.py`. `reference_data.py` establishes the
  dicts these rely on but nothing here re-validates that the rows handed to the loader were actually filtered
  through them — that is an inter-module contract the loader trusts implicitly.
- **Shared state:** `PORTS`, `CARGO_TYPES`, `DIRECTION_CODES` are read-only in every consumer inspected
  (`throughput.py`, `continuity.py`, `loader.py`) — no code path mutates these dicts after import — but
  nothing in this module or its type (a plain mutable `dict`) prevents a future or malicious import from
  doing so, and the mutation would be visible process-wide since module objects are singletons.
- **Invariant coupling:** The pipeline's implicit "every emitted `PortThroughputRow`/`DataQualityFlag` names a
  port/cargo type that exists in the database" invariant (needed by `loader.py`'s FK-resolving upserts,
  L92-93/L102-103/L132) rests entirely on the `PORTS`/`CARGO_TYPES` membership filters applied upstream in
  `throughput.py`, which in turn rest on this module's key/field-agreement precondition. A single mismatch
  anywhere in this file's literals propagates to a `KeyError` at load time rather than an error localized to
  this module.

**Open Questions:**
- unclear; need to inspect `throughput.py::build_direction_rows` more closely (L58) — it hardcodes
  `cargo_type_code="TOTAL"` as a string literal rather than looking it up via `CARGO_TYPES["TOTAL"]`. Whether
  this is intentional (avoiding a dict lookup for a value known to be static) or an inconsistency with the
  rest of the module's "look values up through the dict" convention is not resolved by this file.
- unclear; need to inspect whether any test or startup check asserts `PORTS` key/field agreement and
  `merged_into` referential integrity, since `reference_data.py` itself performs no such assertion and
  `models.Port` (models.py L31-38) has no validator for it.
- unclear; need to inspect whether `continuity.py`'s hardcoded `MERGED_PORT_CODE`/`LEGACY_PORT_CODES`
  (continuity.py L38-39) are covered by a test that would fail if `reference_data.PORTS`'s `merged_into`
  relationships changed without a corresponding edit there — nothing in either file wires them together
  programmatically.
