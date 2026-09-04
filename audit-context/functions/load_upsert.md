# Analysis: src/port_analytics/load/upsert.py

Module-level note: this file builds SQL text and returns it alongside a
separate parameter tuple in a `Statement` NamedTuple (L20-22: `sql: str`,
`params: tuple[Any, ...]`). No function in this module opens a connection or
calls `execute` — that happens in `load/loader.py`, which always calls
`cursor.execute(stmt.sql, stmt.params)` (loader.py L51, L58, L69, L95, L145),
i.e. passes `sql` and `params` as the two separate pyodbc arguments rather
than merging them into one string first. Every SQL string in this module is a
fixed triple-quoted literal (L30-40, L55, L60-70, L86-106, L133-147) —
none of them is built with an f-string, `.format()`, `%`, or `+`
concatenation, and no function parameter (port name, code, flag description,
etc.) is ever written into the `sql` variable. All value interpolation goes
through `?` placeholders and the parallel `params` tuple. This holds for
every function below without exception; each function's section states the
placeholder-count-to-params-count match explicitly since that correspondence
is the only thing standing between "parameterized" and "silently
misaligned."

---

## `build_port_upsert` in src/port_analytics/load/upsert.py (L25-51)

**Purpose:** Builds a MERGE statement that upserts one `Port` into
`dbo.ports` keyed on `eurostat_code`, returning the resulting `port_id` via
`OUTPUT`. This is how the loader obtains surrogate keys for ports before any
throughput row can be inserted (throughput rows FK to `port_id`, per
schema.sql L42-43). The docstring (L26-29) records that
`merged_into_port_id` is deliberately left unset here because it is a
self-referencing FK that can only be resolved after every port row exists;
that is handled by `build_port_merge_link` in a second pass.

**Inputs & Assumptions:**
- `port` (`Port`, a pydantic `BaseModel` — models.py L31-37): `eurostat_code`,
  `port_name`, `country_code`, `un_locode` (optional), `merged_into`
  (optional, unused here). Trust: in the only caller found
  (`loader.upsert_ports`, loader.py L49), `port` is drawn from
  `transform.reference_data.PORTS`, a hardcoded in-repo dict
  (reference_data.py L10-27), not from parsed Eurostat data. Trusted at this
  call site. Nothing in `build_port_upsert` itself restricts `port`'s field
  values (no length or charset check) — pydantic's `Port` model declares the
  fields as plain `str`/`str | None` with no `Field(max_length=...)`
  constraint (models.py L32-36), so nothing in-process would stop an
  oversized or arbitrary string reaching this function if a future caller
  supplied non-reference-data input.
- Preconditions: none enforced by this function; it is a pure string/tuple
  builder and does not validate `port` beyond what pydantic's default (very
  permissive) `str` typing gives it at model-construction time, upstream of
  this call.

**Outputs & Effects:**
- Returns a `Statement(sql, params)` (L51). No I/O, no mutation, no
  connection. The SQL's `OUTPUT inserted.port_id, inserted.eurostat_code;`
  (L39) is what lets the caller retrieve the generated `port_id` after
  `execute` — `build_port_upsert` itself does not run it or see the result.

**Block-by-Block:**

```sql
-- L30-40
MERGE dbo.ports AS target
USING (SELECT ? AS eurostat_code) AS src
ON target.eurostat_code = src.eurostat_code
WHEN MATCHED THEN
    UPDATE SET port_name = ?, country_code = ?, un_locode = ?
WHEN NOT MATCHED THEN
    INSERT (port_name, country_code, un_locode, eurostat_code)
    VALUES (?, ?, ?, ?)
OUTPUT inserted.port_id, inserted.eurostat_code;
```
- **What:** eight `?` placeholders total — one in the `USING` subquery
  (natural-key probe), three in `UPDATE SET`, four in `INSERT VALUES`.
- **Why here:** the natural key (`eurostat_code`) is matched first so a
  second run against the same reference data updates the existing row in
  place instead of violating `UQ_ports_eurostat_code` (schema.sql L15).
- **Assumes:** the caller supplies exactly eight params in the order the
  eight `?` appear, and that `eurostat_code` uniquely identifies a port
  (enforced by the schema's UNIQUE constraint, not by this SQL).
- **Establishes:** idempotent upsert — the module docstring's central claim
  (L7-10) — for the `ports` table only if `params` is ordered to match; that
  ordering is established by the block below, not by this one.
- **Depended on by:** `loader.upsert_ports` (loader.py L46-61), which uses
  the returned `port_id` to build the `port_ids` dict consumed by every
  later upsert call in `load_all`.

```python
# L41-50
params = (
    port.eurostat_code,
    port.port_name,
    port.country_code,
    port.un_locode,
    port.port_name,
    port.country_code,
    port.un_locode,
    port.eurostat_code,
)
```
- **What:** builds the 8-tuple matching, positionally, the 8 `?` in the SQL
  above: `[eurostat_code]` (USING), `[port_name, country_code, un_locode]`
  (UPDATE SET), `[port_name, country_code, un_locode, eurostat_code]`
  (INSERT VALUES).
- **Why here:** pyodbc's `?` paramstyle binds positionally, so this tuple's
  order is load-bearing — a reorder here without a matching reorder in the
  SQL (or vice versa) silently binds the wrong value to the wrong column
  with no error, since all fields are compatible-enough types
  (`str`/`str | None`) to bind without a type error.
- **Assumes:** the SQL text above has exactly 8 `?` in this exact sequence.
  Verified by counting: L32 (1) + L35 (3) + L38 (4) = 8, matches.
- **Establishes:** correct value-to-column binding for this statement.
- **Depended on by:** the executing cursor in loader.py L51.

**Cross-Function Dependencies:**
- Callers: `loader.upsert_ports` (loader.py L46-61) calls this once per
  `Port` in `PORTS.values()` and assumes the returned `Statement.sql` is
  fully parameterized (i.e., safe to execute directly with `stmt.params`)
  and that `row.port_id` will be populated after `execute()` — true only
  because `OUTPUT inserted.port_id` (L39) always returns exactly one row for
  either the `WHEN MATCHED` or `WHEN NOT MATCHED` branch of a MERGE with a
  single-row `USING` source (T-SQL semantics external to this file).
- Shared state: none — this function touches no shared state itself; the
  `ports` table it targets is mutated only when the caller executes the
  returned statement.
- No external calls; no black-box callees.

**Open Questions:**
- Whether any caller other than `loader.upsert_ports` will ever pass a
  `Port` built from parsed/external data rather than the static
  `reference_data.PORTS` dict — if so, the absence of length/charset
  validation on `port_name`/`country_code`/`un_locode` here (vs. the
  schema's `NVARCHAR(100)`/`CHAR(2)`/`CHAR(5)` limits, schema.sql L10-12)
  becomes relevant; currently `nothing found` enforcing those limits before
  they reach the database driver.

---

## `build_port_merge_link` in src/port_analytics/load/upsert.py (L54-56)

**Purpose:** Second-pass statement that sets `merged_into_port_id` on a port
row once the target port's real surrogate key is known, completing what
`build_port_upsert` deliberately deferred (L26-29 docstring cross-reference).

**Inputs & Assumptions:**
- `eurostat_code` (`str`): the natural key of the port being marked as
  merged-away. Trust: caller-supplied; in the only call site
  (loader.py L57) it is `port.eurostat_code` from the same static
  `PORTS` dict as above.
- `merged_into_port_id` (`int`): the surrogate `port_id` of the port it
  merged into. Trust: caller-supplied; in loader.py L57 it is
  `port_ids[port.merged_into]`, a dict lookup that assumes
  `port.merged_into` (when not `None`) is itself a key already present in
  `port_ids` — established only if `build_port_upsert` has already been run,
  and successfully returned a row, for the target port. Precondition
  established by the caller's two-pass loop structure (loader.py L49-58: all
  ports are upserted in the first loop before any merge link is set in the
  second), not by this function.
- Preconditions: none enforced here; this function does not check that
  `eurostat_code` exists in `dbo.ports` or that `merged_into_port_id`
  references a valid row — both are left to the FK constraint
  `FK_ports_merged_into` (schema.sql L16-17) at execution time.

**Outputs & Effects:**
- Returns `Statement(sql, params)` (L56). No I/O.

**Block-by-Block:**

```sql
-- L55
UPDATE dbo.ports SET merged_into_port_id = ? WHERE eurostat_code = ?;
```
- **What:** two `?` placeholders, one for the new FK value, one for the
  `WHERE` key.
- **Why here:** unconditional `UPDATE` rather than a MERGE, since the target
  row is known to already exist by the time this runs (see precondition
  above) — there is no insert branch.
- **Assumes:** exactly one row in `dbo.ports` has this `eurostat_code`
  (true if `UQ_ports_eurostat_code`, schema.sql L15, holds); if zero rows
  match, the `UPDATE` is a silent no-op (T-SQL does not error on a
  zero-row `UPDATE`) — nothing in this function or its caller checks
  `cursor.rowcount` after executing it (loader.py L58 discards the
  execute result).
- **Establishes:** the merge-chain FK, once executed.
- **Depended on by:** any downstream query that follows
  `merged_into_port_id` to consolidate legacy ports into their successor
  (outside this module).

```python
# L56
return Statement(sql, params=(merged_into_port_id, eurostat_code))
```
- **What:** binds `merged_into_port_id` to the first `?`, `eurostat_code` to
  the second, matching the SQL's `SET ... = ?` then `WHERE ... = ?` order.
- **Assumes:** SQL and tuple stay in this same two-item order.
- **Establishes:** correct positional binding.

**Cross-Function Dependencies:**
- Callers: `loader.upsert_ports` (loader.py L55-59), only for ports where
  `port.merged_into is not None`; assumes `port_ids[port.merged_into]`
  succeeds (a plain dict `[]` lookup, L57) — if the merge target's
  `eurostat_code` was never upserted (e.g., typo in `merged_into`, or the
  target port missing from `PORTS`), this raises `KeyError` in the caller,
  not in this function.
- Shared state: mutates `dbo.ports` at execution time only.
- No external calls.

**Open Questions:**
- Whether the caller should check `cursor.rowcount == 1` after executing
  this to detect the zero-row-match case described above; `nothing found`
  in loader.py doing so (L58 discards the result of `cursor.execute`).

---

## `build_cargo_type_upsert` in src/port_analytics/load/upsert.py (L59-77)

**Purpose:** Structurally identical to `build_port_upsert` but for
`dbo.cargo_types`, keyed on `cargo_type_code`. Supplies the `cargo_type_id`
surrogate keys that throughput rows FK to (schema.sql L44-45).

**Inputs & Assumptions:**
- `cargo_type` (`CargoType`, models.py L40-42): `cargo_type_code`,
  `cargo_type_name`, both plain `str` with no length/charset constraint at
  the pydantic layer. Trust: at the only call site
  (`loader.upsert_cargo_types`, loader.py L64-73), sourced from the static
  `transform.reference_data.CARGO_TYPES` dict — trusted, in-repo constant
  data, not parsed external input.
- Preconditions: none enforced by this function.

**Outputs & Effects:**
- Returns `Statement(sql, params)` (L77). No I/O.

**Block-by-Block:**

```sql
-- L60-70
MERGE dbo.cargo_types AS target
USING (SELECT ? AS cargo_type_code) AS src
ON target.cargo_type_code = src.cargo_type_code
WHEN MATCHED THEN
    UPDATE SET cargo_type_name = ?
WHEN NOT MATCHED THEN
    INSERT (cargo_type_name, cargo_type_code)
    VALUES (?, ?)
OUTPUT inserted.cargo_type_id, inserted.cargo_type_code;
```
- **What:** four `?` placeholders: 1 (USING) + 1 (UPDATE SET) + 2 (INSERT
  VALUES) = 4, matching the four-element `params` tuple below.
- **Why here:** same upsert-on-natural-key pattern as `build_port_upsert`,
  relying on `UQ_cargo_types_code` (schema.sql L27) to make the match
  unambiguous.
- **Assumes:** `params` supplies exactly these four values in this order.
- **Establishes:** idempotent upsert for `cargo_types`, contingent on the
  params ordering below.
- **Depended on by:** `loader.upsert_cargo_types` (loader.py L64-73), which
  reads `row.cargo_type_id` from the `OUTPUT` and builds
  `cargo_type_ids`, later required by `build_throughput_upsert`'s caller.

```python
# L71-76
params = (
    cargo_type.cargo_type_code,
    cargo_type.cargo_type_name,
    cargo_type.cargo_type_name,
    cargo_type.cargo_type_code,
)
```
- **What:** 4-tuple: `[cargo_type_code]` (USING), `[cargo_type_name]`
  (UPDATE SET), `[cargo_type_name, cargo_type_code]` (INSERT VALUES) —
  matches the SQL's placeholder order exactly.
- **Assumes:** the SQL text's placeholder count and order stay in sync with
  this tuple.
- **Establishes:** correct positional binding.

**Cross-Function Dependencies:**
- Callers: `loader.upsert_cargo_types` (loader.py L64-73); same
  single-row-per-MERGE assumption as `build_port_upsert`'s caller (relies on
  `cursor.fetchone()` at L70 always returning a row).
- Shared state: mutates `dbo.cargo_types` at execution time only.
- No external calls.

**Open Questions:**
- None beyond the general reference-data-trust note in the module preamble.

---

## `build_throughput_upsert` in src/port_analytics/load/upsert.py (L80-121)

**Purpose:** Builds the MERGE for `dbo.port_throughput`, keyed on the
five-column natural key `(port_id, cargo_type_id, year, direction, source)`
(matching `UQ_throughput_natural_key`, schema.sql L48-49). Its `OUTPUT`
clause returns both the pre- and post-update `gross_weight_tonnes` so the
caller can distinguish an unchanged re-run from a genuine Eurostat revision
(docstring L81-85, consumed at loader.py L100-118).

**Inputs & Assumptions:**
- `row` (`PortThroughputRow`, models.py L55-63): `year` (`int`), `direction`
  (`Direction` enum), `gross_weight_tonnes` (`float`), `source` (`str`).
  Trust: at the only call site (`loader.upsert_throughput_rows`,
  loader.py L91-95), `row` comes from the `rows: list[PortThroughputRow]`
  parameter threaded through `load_all` from the transform layer
  (ultimately derived from Eurostat data, per transform/throughput.py and
  transform/continuity.py) — this is the one input in this module that
  originates from parsed external data rather than a static dict, though
  `source` itself is always one of a small set of constant dataset codes
  (`DIRECTION_DATASET_CODE`, `CARGO_DATASET_CODE`, `DERIVED_SOURCE`; found
  via grep of transform/throughput.py L62, L119 and transform/continuity.py
  L90), not a raw pass-through of untrusted text.
- `port_id` (`int`), `cargo_type_id` (`int`): caller-supplied surrogate
  keys. Trust: in loader.py L92-93, looked up from `port_ids[row.port_code]`
  and `cargo_type_ids[row.cargo_type_code]` — plain dict `[]` lookups that
  raise `KeyError` in the caller (not here) if `row.port_code` or
  `row.cargo_type_code` was never upserted.
- Preconditions: none enforced by this function itself.

**Outputs & Effects:**
- Returns `Statement(sql, params)` (L121). No I/O.

**Block-by-Block:**

```sql
-- L86-106
MERGE dbo.port_throughput AS target
USING (
    SELECT ? AS port_id, ? AS cargo_type_id, ? AS year, ? AS direction, ? AS source
) AS src
ON target.port_id = src.port_id
    AND target.cargo_type_id = src.cargo_type_id
    AND target.year = src.year
    AND target.direction = src.direction
    AND target.source = src.source
WHEN MATCHED THEN
    UPDATE SET gross_weight_tonnes = ?
WHEN NOT MATCHED THEN
    INSERT (port_id, cargo_type_id, year, direction, gross_weight_tonnes, source)
    VALUES (?, ?, ?, ?, ?, ?)
OUTPUT
    $action,
    inserted.throughput_id,
    deleted.gross_weight_tonnes,
    inserted.gross_weight_tonnes;
```
- **What:** twelve `?` placeholders: 5 (USING) + 1 (UPDATE SET) + 6 (INSERT
  VALUES) = 12.
- **Why here:** all five natural-key columns are matched with plain `=`
  (no NULL-safe handling, unlike `build_flag_upsert` below) — consistent
  with every one of `port_id, cargo_type_id, year, direction, source` being
  `NOT NULL` in the schema (schema.sql L35-40), so NULL-safe equality is
  unnecessary here.
- **Assumes:** the 12-element `params` tuple below supplies values in this
  exact order; also assumes `$action` plus the three `inserted`/`deleted`
  columns always yield exactly one output row per MERGE execution (T-SQL
  guarantee for a single-row `USING` source, external to this file).
- **Establishes:** idempotent upsert for `port_throughput`, and — via
  `deleted.gross_weight_tonnes` being `NULL` on the insert path and the old
  value on the update path — the "did the value actually change"
  distinction the caller relies on (loader.py L100: `old_value is not
  None and abs(float(old_value) - float(new_value)) > 0.01`).
- **Depended on by:** `loader.upsert_throughput_rows` (loader.py L91-121),
  which builds `throughput_ids` and revision flags directly from the four
  `OUTPUT` columns in the order `action, throughput_id, old_value,
  new_value` (L96) — that unpacking order must match `$action,
  inserted.throughput_id, deleted.gross_weight_tonnes,
  inserted.gross_weight_tonnes` (L102-105) positionally; nothing named
  cross-checks this, it is purely positional on both sides of the module
  boundary.

```python
# L107-120
params = (
    port_id,
    cargo_type_id,
    row.year,
    row.direction.value,
    row.source,
    row.gross_weight_tonnes,
    port_id,
    cargo_type_id,
    row.year,
    row.direction.value,
    row.gross_weight_tonnes,
    row.source,
)
```
- **What:** 12-tuple. Positions 1-5 feed the `USING` clause; position 6
  feeds `UPDATE SET gross_weight_tonnes = ?`; positions 7-12 feed `INSERT
  VALUES (port_id, cargo_type_id, year, direction, gross_weight_tonnes,
  source)` — note the INSERT's value order is `(..., gross_weight_tonnes,
  source)` (L100) and the tuple's last two elements are, correspondingly,
  `row.gross_weight_tonnes` then `row.source` (L118-119) — the two
  five/six-element groups are *not* identically ordered internally (the
  USING block ends `..., source` at position 5 while the INSERT block ends
  `..., gross_weight_tonnes, source` at positions 11-12), but each group is
  independently correct against its corresponding placeholder sequence.
- **Assumes:** `row.direction.value` yields the plain string form of the
  `Direction` enum (`Direction` is a `StrEnum`, models.py L13), matching
  the `NVARCHAR(10)` `direction` column.
- **Establishes:** correct positional binding for all 12 placeholders.

**Cross-Function Dependencies:**
- Callers: `loader.upsert_throughput_rows` (loader.py L76-121). Assumes (a)
  `port_id` and `cargo_type_id` passed in were already validated to exist
  as real FK targets by the earlier `upsert_ports`/`upsert_cargo_types`
  calls — enforced only by the `FK_throughput_port`/
  `FK_throughput_cargo_type` constraints at execute time (schema.sql
  L42-45), not by this function; (b) the four-column `OUTPUT` unpacks
  positionally into `action, throughput_id, old_value, new_value` exactly
  as built here.
- Shared state: mutates `dbo.port_throughput`; the `throughput_id` values
  it yields (via the caller) become the `throughput_id` FK input consumed
  later by `build_flag_upsert`'s caller (`loader.upsert_flags`,
  loader.py L124-146).
- No external calls; no black-box callees inside this function.

**Open Questions:**
- Whether `row.source`, though currently always one of a few constant
  dataset codes at every call site found, is validated anywhere against
  the `NVARCHAR(50)` column width (schema.sql L40) before reaching this
  function — `nothing found` doing so in this module; the column-width
  question is answered the same way as for `build_port_upsert`: not
  enforced here, deferred to the driver/database.

---

## `build_flag_upsert` in src/port_analytics/load/upsert.py (L124-159)

**Purpose:** Builds an insert-only MERGE for `dbo.data_quality_flags`,
recording a fact about the data (a missing year, a revised estimate, etc.)
rather than a value that should ever be overwritten in place (docstring
L129-132).

**Inputs & Assumptions:**
- `flag` (`DataQualityFlag`, models.py L66-71): `flag_type` (`FlagType`
  enum), `description` (`str`), `resolution` (`str`). Trust: at the only
  call site (`loader.upsert_flags`, loader.py L124-146), `flags` is threaded
  from `load_all`'s `flags` parameter plus `revision_flags` built in
  `upsert_throughput_rows` (loader.py L104-118) — descriptions are f-strings
  built from `port_name`/`cargo_name` (drawn from the static reference
  dicts) and numeric `old_value`/`new_value`, not raw pass-through of
  external text (loader.py L108-112); other flag descriptions originate in
  transform/throughput.py and transform/continuity.py, not inspected in
  depth here.
- `port_id` (`int | None`), `throughput_id` (`int | None`): caller-supplied,
  both may legitimately be `None` (e.g., a flag about a port with no
  matching throughput row yet — loader.py L141-143 comment: "None if the
  referenced row doesn't exist — the common case").
- Preconditions: none enforced by this function.

**Outputs & Effects:**
- Returns `Statement(sql, params)` (L159). No I/O.

**Block-by-Block:**

```sql
-- L133-147
MERGE dbo.data_quality_flags AS target
USING (
    SELECT ? AS throughput_id, ? AS port_id, ? AS flag_type, ? AS description
) AS src
ON (target.throughput_id = src.throughput_id
        OR (target.throughput_id IS NULL AND src.throughput_id IS NULL))
    AND (target.port_id = src.port_id
        OR (target.port_id IS NULL AND src.port_id IS NULL))
    AND target.flag_type = src.flag_type
    AND target.description = src.description
WHEN NOT MATCHED THEN
    INSERT (throughput_id, port_id, flag_type, description, resolution)
    VALUES (?, ?, ?, ?, ?);
```
- **What:** nine `?` placeholders: 4 (USING) + 5 (INSERT VALUES) = 9.
  No `WHEN MATCHED` branch at all.
- **Why here:** the NULL-safe `OR (... IS NULL AND ... IS NULL)` pattern on
  `throughput_id` and `port_id` (L138-141) exists because both columns are
  nullable (schema.sql L57-58) and plain `=` never matches `NULL = NULL` in
  T-SQL (docstring L131-132 states this explicitly) — without it, two flags
  that are both genuinely port-less/throughput-less would never be
  recognized as the same fact and would duplicate on every re-run.
- **Assumes:** `flag_type` and `description` are compared with plain `=`
  (L142-143), not NULL-safe — both are `NOT NULL` columns
  (schema.sql L59-60) and both model fields are non-Optional `str`
  (models.py L67-68), so this is consistent with the schema, but nothing at
  the Python type level (a plain `str` accepts `""`) or in this function
  guarantees non-empty content; an empty-string `description` would still
  compare correctly via `=`, just not via a `NULL`-safety path.
- **Establishes:** at-most-one-row-per-identity-tuple, where identity is
  `(throughput_id, port_id, flag_type, description)` — notably
  **excluding** `resolution` from the match key.
- **Depended on by:** the idempotency claim in the docstring (L129-132) —
  but only insofar as `resolution` text is treated as non-identifying. A
  consequence structurally present here: because there is no `WHEN
  MATCHED` branch, if a row with the same
  `(throughput_id, port_id, flag_type, description)` already exists, this
  statement is a silent no-op on that row — a new `resolution` value for an
  otherwise-identical flag is never written, and nothing surfaces that to
  the caller (no `OUTPUT` clause at all on this statement, unlike the other
  three MERGEs in this module).

```python
# L148-158
params = (
    throughput_id,
    port_id,
    flag.flag_type.value,
    flag.description,
    throughput_id,
    port_id,
    flag.flag_type.value,
    flag.description,
    flag.resolution,
)
```
- **What:** 9-tuple: positions 1-4 feed the `USING` clause (the ON-clause
  comparisons reference `src.*`, so these four values are also what get
  compared against `target.*`); positions 5-9 feed `INSERT VALUES
  (throughput_id, port_id, flag_type, description, resolution)`.
- **Assumes:** `flag.flag_type.value` yields the plain string form of the
  `FlagType` `StrEnum` (models.py L19), matching the `NVARCHAR(30)`
  `flag_type` column.
- **Establishes:** correct positional binding for all 9 placeholders,
  including the deliberate repetition of `throughput_id`, `port_id`,
  `flag.flag_type.value`, `flag.description` between the USING probe and
  the INSERT values (both must carry the identity fields; `resolution` is
  supplied only once, at the very end, since it is insert-only data with no
  corresponding USING-clause slot).

**Cross-Function Dependencies:**
- Callers: `loader.upsert_flags` (loader.py L124-146). Assumes (a)
  `port_id`/`throughput_id` passed in are either `None` or valid FK targets
  — enforced by `FK_flags_throughput`/`FK_flags_port`
  (schema.sql L63-66) at execute time, not by this function; (b) no
  return value is needed from this statement (loader.py L145 calls
  `cursor.execute` and never calls `fetchone()`, consistent with there
  being no `OUTPUT` clause here) — unlike the other three upsert builders
  in this module, whose callers all do call `fetchone()`.
- Shared state: mutates `dbo.data_quality_flags`. Reads (via the caller, not
  this function) the `throughput_ids` map produced by
  `build_throughput_upsert`'s caller and the `port_ids` map produced by
  `build_port_upsert`'s caller — this function is the last stage of a
  three-stage key-resolution chain (`ports` → `throughput` → `flags`) but
  has no way to detect whether that chain was completed correctly; it only
  receives whatever `int | None` the caller resolved.
- No external calls.

**Open Questions:**
- Whether the exclusion of `resolution` from the MERGE's match/ON clause
  (L138-143) is intended to mean "the first `resolution` recorded for a
  given flag identity wins forever," or whether some other code path is
  expected to update `resolution` later — `nothing found` in this module or
  in `loader.py` that ever updates an existing flag row's `resolution`.
- Whether an empty-string `description` (permitted by the `str` type,
  models.py L68) is ever produced upstream — not traced beyond the grep of
  `description=` call sites in transform/throughput.py and
  transform/continuity.py.
