# Function analyses: src/port_analytics/load/loader.py

Module purpose per its docstring (L1-7): applies schema.sql, then upserts ports, cargo
types, throughput rows, and flags via MERGE statements built in `upsert.py`. All SQL text
and params come from `upsert.py` (pure, no DB access — upsert.py L1-11); loader.py only
sequences `cursor.execute` / `fetchone` / `commit` calls against a `pyodbc.Connection`
obtained from `connection.py`'s `connect()`.

---

## `apply_schema` in src/port_analytics/load/loader.py (L39-43)

**Purpose:** Applies `schema.sql` (idempotent `CREATE TABLE IF NOT EXISTS`-style DDL,
guarded with `IF OBJECT_ID(...) IS NULL` per schema.sql L6,21,31,53) to the target
database. Every other function in this module depends on the four tables it creates
existing with the exact column/constraint shapes in schema.sql.

**Inputs & Assumptions:**
- `conn` (pyodbc.Connection): trusted — constructed by `connection.py:connect()` and
  passed in by the caller (`load_all`, and transitively `cli.py` L47-48). No validation
  here that `conn` is open or usable.
- Implicit: reads `SCHEMA_PATH` (L26, `Path(__file__).parent / "schema.sql"`) from disk
  at call time — a fixed, repo-controlled file, not user input.
- Precondition: `schema.sql` must be valid as a single ODBC batch (no `GO` separators,
  which are SSMS-only, not T-SQL). Established only by the file's own content
  (schema.sql L1-69 has none); nothing in `apply_schema` checks this — a syntax error
  surfaces only as a `pyodbc.Error` from `cursor.execute`.

**Outputs & Effects:**
- Executes the full DDL script as one `cursor.execute(sql)` (L42) and commits (L43).
- No return value. Side effect is purely schema creation/no-op on the connection's
  database.

**Block-by-Block:**

```python
# L40-43
sql = SCHEMA_PATH.read_text(encoding="utf-8")
cursor = conn.cursor()
cursor.execute(sql)
conn.commit()
```
- **What:** Reads the schema file and executes it as a single batch, then commits.
- **Why here:** Must run before any upsert, since every later `MERGE` targets a table
  this creates.
- **Assumes:** `cursor.execute` either succeeds entirely or raises; there is no partial
  application of a single `execute` call to reason about. Assumes autocommit is off
  (see `connection.py` notes below) so `conn.commit()` is meaningful/necessary.
- **Establishes:** If it returns without raising, the four tables in schema.sql exist
  and are committed. If it raises, propagates uncaught (no try/except in this function
  or its caller `load_all`) — the commit at L43 never runs, so any DDL from *this* call
  is not committed (though `IF OBJECT_ID IS NULL` guards make partial DDL from one
  `execute` call unlikely to matter, since SQL Server treats each guarded `CREATE TABLE`
  as its own statement inside the batch).
- **Depended on by:** Every subsequent function in the module (`upsert_ports` etc.)
  assumes the tables exist with the FK/unique constraints in schema.sql — this is what
  makes the later MERGE natural-key logic (upsert.py) idempotent.

**Cross-Function Dependencies:**
- Callee: none internal (reads a file, calls `pyodbc.Connection` methods directly).
- `pyodbc` (external, source not inspected — C extension): `cursor.execute` and
  `conn.commit()` are trusted to do what their names imply. Failure mode on a bad
  connection or bad SQL is an exception; not verified from source what state `conn` is
  left in afterward.
- Callers: `load_all` (L154) calls this first, unconditionally, before touching
  `port_ids`/`cargo_type_ids`. It assumes `apply_schema` either fully succeeds or raises
  — there is no code path where `load_all` proceeds after a partial/failed schema apply.

**Open Questions:**
- unclear; need to inspect pyodbc's C source or docs to confirm a single `cursor.execute()`
  call is guaranteed to run schema.sql's four `IF OBJECT_ID ... BEGIN ... END;` blocks as
  one batch rather than requiring per-statement execution (ODBC Driver 18 for SQL Server
  behavior with multi-statement batches).

---

## `upsert_ports` in src/port_analytics/load/loader.py (L46-61)

**Purpose:** Upserts the six static ports from `reference_data.PORTS` (reference_data.py
L10-27) into `dbo.ports`, then resolves each merged port's `merged_into_port_id` FK in a
second pass. Produces the `eurostat_code -> port_id` map every later function in the
pipeline needs to translate domain codes into DB surrogate keys.

**Inputs & Assumptions:**
- `conn` (pyodbc.Connection): trusted, same as `apply_schema`.
- Implicit: reads the module-level `PORTS` dict (reference_data.py L10-27) — fixed,
  code-controlled, not user input. Iteration order is dict-insertion order.
- Precondition: `dbo.ports` exists with `UQ_ports_eurostat_code` unique constraint
  (schema.sql L15). Established by `apply_schema` having run first — not verified here;
  if `upsert_ports` were called without `apply_schema` (it is not, in this module's only
  call path via `load_all` L154-155), every `cursor.execute` would raise on a missing
  table.
- Precondition (unenforced): every `port.merged_into` value must be a key already
  present in `PORTS` (so `port_ids[port.merged_into]` at L57 resolves). Established only
  by manual consistency of reference_data.py (both `BE_0BEANR` and `BE_0BEZEE` point to
  `BE_0BE003`, which is itself a key in `PORTS` — reference_data.py L11-23); nothing in
  `upsert_ports` checks this before indexing.

**Outputs & Effects:**
- Returns `dict[str, int]` mapping `eurostat_code -> port_id` (L61), covering every
  `Port` in `PORTS` (populated in the first loop, L49-53, regardless of merge status).
- Writes/commits one MERGE per port plus one UPDATE per merged port, all in a single
  transaction committed once at L60.

**Block-by-Block:**

```python
# L49-53
for port in PORTS.values():
    stmt = build_port_upsert(port)
    cursor.execute(stmt.sql, stmt.params)
    row = cursor.fetchone()
    port_ids[port.eurostat_code] = row.port_id
```
- **What:** For every static port, MERGE-upsert it and capture the resulting `port_id`.
- **Why here:** Must finish for *all* ports before the second loop, since a merge target
  might be encountered before its dependents in dict order otherwise.
- **Assumes:** `cursor.fetchone()` returns exactly one row with a `.port_id` attribute
  for every execution. This rests on `build_port_upsert`'s MERGE (upsert.py L30-40)
  having `OUTPUT inserted.port_id, inserted.eurostat_code` with no `WHEN NOT MATCHED BY
  SOURCE` clause — combined with `UQ_ports_eurostat_code` (schema.sql L15) guaranteeing
  the `USING (SELECT ? AS eurostat_code)` single-row source matches at most one target
  row, exactly one of `WHEN MATCHED`/`WHEN NOT MATCHED` always fires, so the MERGE always
  affects exactly one row and OUTPUTs exactly one row. **Nothing in `upsert_ports` checks
  `row is not None` before `row.port_id`** (L53) — if this invariant were ever violated
  (e.g., a trigger suppressing OUTPUT, or a driver-level buffering issue), this line
  raises `AttributeError` on `None`, uncaught.
- **Establishes:** `port_ids` fully covers every key in `PORTS` before the second loop
  begins.
- **Depended on by:** The merge-link loop below, and every downstream function
  (`upsert_throughput_rows`, `upsert_flags`) that indexes `port_ids` by `row.port_code` /
  `flag.port_code`.

```python
# L55-58
for port in PORTS.values():
    if port.merged_into is not None:
        link = build_port_merge_link(port.eurostat_code, port_ids[port.merged_into])
        cursor.execute(link.sql, link.params)
```
- **What:** For each merged port, sets `merged_into_port_id` to the target port's
  surrogate key via a plain UPDATE (upsert.py L54-56, not a MERGE — no OUTPUT, no
  `fetchone` call here, correctly).
- **Why here:** Deferred to a second pass specifically because `merged_into_port_id` is
  a self-referencing FK that needs every port's real `port_id` first (upsert.py L26-29
  docstring) — this is the two-pass structure that makes merge order irrelevant.
- **Assumes:** `port_ids[port.merged_into]` — `port.merged_into` is a key in `port_ids`.
  True given the precondition above and the first loop's full-coverage guarantee; if
  violated, raises `KeyError`, uncaught.
- **Establishes:** `dbo.ports.merged_into_port_id` correctly populated for merged ports.
- **Depended on by:** Nothing further in this module (no code reads
  `merged_into_port_id` back); this is terminal state for downstream consumers of the
  database (e.g. reporting queries, out of scope here).

```python
# L60-61
conn.commit()
return port_ids
```
- **What:** Commits both loops' writes as one transaction, then returns the map.
- **Assumes:** Nothing raised earlier in the function; if it did, this commit is never
  reached and none of this function's own writes land (autocommit is off — see
  `connection.py` notes).
- **Establishes:** `port_ids` returned to the caller is only ever a fully-committed,
  fully-populated map — there's no code path returning a partial map.

**Cross-Function Dependencies:**
- Callee `build_port_upsert` (internal, upsert.py L25-51): loader.py depends on it to
  produce SQL whose `?` placeholder count/order matches `stmt.params` exactly (8 `?`s at
  upsert.py L32,35,38 vs. 8-tuple at L41-50) and whose OUTPUT column is literally named
  `port_id` (unaliased `inserted.port_id`, L39) so that `row.port_id` attribute access
  works. Nothing in loader.py verifies this alignment; a change to one without the other
  would surface only as a runtime `pyodbc` error or, worse, silently misaligned params if
  counts happen to still match.
- Callee `build_port_merge_link` (internal, upsert.py L54-56): a plain parameterized
  UPDATE, no OUTPUT — loader.py correctly does not call `fetchone()` after it.
- Callers: `load_all` (L155) uses the returned `port_ids` as an opaque, trusted,
  fully-populated map for the rest of the pipeline — no re-validation.
- Shared state: `port_ids` is passed by value (dict reference) into
  `upsert_throughput_rows` and `upsert_flags`; those functions index it by
  `row.port_code` / `flag.port_code`, values that originate outside this function (from
  `rows`/`flags` passed into `load_all`), not from `PORTS` — see Open Questions on that
  coupling below and in `upsert_throughput_rows`.

**Open Questions:**
- None specific to this function beyond the shared cross-module ones noted in
  `upsert_throughput_rows` and `upsert_flags`.

---

## `upsert_cargo_types` in src/port_analytics/load/loader.py (L64-73)

**Purpose:** Structurally identical to `upsert_ports` minus the merge-link second pass —
upserts the seven static cargo types from `reference_data.CARGO_TYPES`
(reference_data.py L38-46) and returns the `cargo_type_code -> cargo_type_id` map.

**Inputs & Assumptions:**
- `conn`: same trust level as above.
- Implicit: reads module-level `CARGO_TYPES` dict — fixed, code-controlled.
- Precondition: `dbo.cargo_types` exists with `UQ_cargo_types_code` (schema.sql L27),
  established by `apply_schema` running first (enforced only by call order in
  `load_all`, L154 before L156).

**Outputs & Effects:**
- Returns `dict[str, int]` covering every key in `CARGO_TYPES` (L73).
- One MERGE per cargo type, all committed together at L72.

**Block-by-Block:**

```python
# L67-71
for cargo_type in CARGO_TYPES.values():
    stmt = build_cargo_type_upsert(cargo_type)
    cursor.execute(stmt.sql, stmt.params)
    row = cursor.fetchone()
    cargo_type_ids[cargo_type.cargo_type_code] = row.cargo_type_id
```
- **What:** MERGE-upsert each cargo type, capture `cargo_type_id`.
- **Assumes:** Same fetchone-always-returns-a-row assumption as `upsert_ports` L52-53,
  resting on `UQ_cargo_types_code` (schema.sql L27) guaranteeing the MERGE always matches
  or inserts exactly one row (upsert.py L60-70). No `row is not None` check before
  `row.cargo_type_id` — `AttributeError` on `None`, uncaught, is the failure mode if that
  invariant is ever broken.
- **Establishes:** `cargo_type_ids` fully covers `CARGO_TYPES`'s keys once the loop
  completes without raising.
- **Depended on by:** `upsert_throughput_rows`, which indexes this map by
  `row.cargo_type_code` for FK resolution.

```python
# L72-73
conn.commit()
return cargo_type_ids
```
- **What/Assumes/Establishes:** Same shape as `upsert_ports`'s final two lines — commit
  is all-or-nothing for this function's own writes; return value is only ever
  fully-populated.

**Cross-Function Dependencies:**
- Callee `build_cargo_type_upsert` (internal, upsert.py L59-77): same params-order/OUTPUT
  naming trust relationship as `build_port_upsert` above (4 `?`s, OUTPUT
  `inserted.cargo_type_id` unaliased).
- Callers: `load_all` (L156) treats the return value the same way it treats `port_ids` —
  opaque, trusted, fully populated.

**Open Questions:** none beyond the shared cross-module ones below.

---

## `upsert_throughput_rows` in src/port_analytics/load/loader.py (L76-121)

**Purpose:** Upserts every transformed throughput row, resolving `port_code`/
`cargo_type_code` to surrogate keys via the maps from `upsert_ports`/`upsert_cargo_types`,
and — per the docstring (L82-86) — detects revisions (a MERGE `UPDATE` whose value
changed materially from what's already stored) by comparing the MERGE's `OUTPUT`
old/new values. This is the mechanism that lets a re-run distinguish "unchanged data,
re-loaded" from "Eurostat revised this figure."

**Inputs & Assumptions:**
- `conn`: trusted, as above.
- `rows` (list[PortThroughputRow]): semi-trusted — produced by `transform_all` upstream
  (cli.py L17, L44) from ingested Eurostat data; not raw external input by the time it
  reaches this function, but not validated *here* either.
- `port_ids`, `cargo_type_ids` (dict[str,int]): trusted internal state, but their
  keyspace is exactly `PORTS`/`CARGO_TYPES` (6 and 7 entries respectively) — **not**
  derived from `rows` at all.
- Precondition (unenforced, load-bearing): every `row.port_code` in `rows` must be a key
  in `port_ids` (i.e., in `PORTS`), and every `row.cargo_type_code` must be a key in
  `cargo_type_ids` (i.e., in `CARGO_TYPES`). Nothing in this function validates
  membership before indexing (L92-93). `reference_data.py` explicitly documents that
  `'UNK'` cargo type is deliberately excluded from `CARGO_TYPES` (reference_data.py
  L35-37) — if `transform_all` ever emitted a row with `cargo_type_code="UNK"` (or any
  port/cargo code outside the reference tables), the failure surfaces here as a
  `KeyError`, not at the point the bad code was introduced upstream. This is the single
  largest cross-module trust boundary in the file: loader.py has zero defense against
  `rows` containing codes outside the two static reference dicts.

**Outputs & Effects:**
- Returns `(throughput_ids, revision_flags)` (L121):
  `throughput_ids: dict[ThroughputKey, int]` mapping `(port_code, cargo_type_code, year,
  direction.value) -> throughput_id`, and `revision_flags: list[DataQualityFlag]` of
  `REVISED_ESTIMATE` flags.
- One MERGE per row in `rows`, all committed together at L120.

**Block-by-Block:**

```python
# L91-96
for row in rows:
    port_id = port_ids[row.port_code]
    cargo_type_id = cargo_type_ids[row.cargo_type_code]
    stmt = build_throughput_upsert(row, port_id, cargo_type_id)
    cursor.execute(stmt.sql, stmt.params)
    action, throughput_id, old_value, new_value = cursor.fetchone()
```
- **What:** Resolve FKs, MERGE the row, unpack the 4-column OUTPUT.
- **Assumes:** (a) membership preconditions above; (b) `cursor.fetchone()` returns
  exactly one row with exactly 4 fields matching, in order, `$action,
  inserted.throughput_id, deleted.gross_weight_tonnes, inserted.gross_weight_tonnes`
  (upsert.py L101-105) — grounded in `UQ_throughput_natural_key` (schema.sql L48-49) on
  `(port_id, cargo_type_id, year, direction, source)` matching *exactly* the MERGE's `ON`
  clause (upsert.py L91-95), so at most one target row matches and exactly one action
  fires. Unlike `upsert_ports`/`upsert_cargo_types` (attribute access, `AttributeError`
  on `None`), this line unpacks a 4-tuple positionally — a `None` from `fetchone()` here
  raises `TypeError` instead, a different failure shape for the same underlying
  invariant.
- **Establishes:** `port_id`/`cargo_type_id` are valid FKs for this row's MERGE, given
  the membership precondition held.
- **Depended on by:** Every line below in this iteration.

```python
# L97-98
key = (row.port_code, row.cargo_type_code, row.year, row.direction.value)
throughput_ids[key] = throughput_id
```
- **What:** Records the natural-key -> surrogate-key mapping.
- **Why here:** `ThroughputKey` (L28) is a 4-tuple whose field order is an implicit
  contract with `upsert_flags`, which builds the identical tuple shape from
  `flag.throughput_ref` (L135-140) to look up `throughput_id`. Both sides must construct
  the tuple in this exact order or lookups silently miss.
- **Assumes:** No two rows in `rows` share the same `(port_code, cargo_type_code, year,
  direction)` — note `source` is part of the DB natural key (schema.sql L48-49,
  upsert.py L91-95) but **not** part of `ThroughputKey` (L28 has no `source` field). If
  two rows in `rows` share `(port_code, cargo_type_code, year, direction)` but differ in
  `source` (plausible — `source` distinguishes real Eurostat datasets from
  `derived_sum:...` computed rows, per PortThroughputRow docstring, models.py L61-63),
  they are two distinct DB rows (different natural key, different `throughput_id`) but
  the **same** `ThroughputKey` here — the second overwrites the first in `throughput_ids`
  silently (dict assignment, no check). Nothing in this function or `ThroughputKey`'s
  definition prevents or detects this collision.
- **Establishes:** `throughput_ids[key]` reflects whichever row with that
  4-tuple was processed *last* in `rows`, if a `source` collision occurred.
- **Depended on by:** `upsert_flags`'s lookup at L143, and `load_all`'s use of
  `throughput_ids` when calling `upsert_flags` (L159).

```python
# L100-118
value_changed = old_value is not None and abs(float(old_value) - float(new_value)) > 0.01
if action == "UPDATE" and value_changed:
    port_name = PORTS[row.port_code].port_name
    cargo_name = CARGO_TYPES[row.cargo_type_code].cargo_type_name
    revision_flags.append(DataQualityFlag(...))
```
- **What:** Flags a `REVISED_ESTIMATE` when a MERGE `UPDATE` changed the stored value by
  more than a 0.01-tonne tolerance.
- **Assumes:** `old_value`/`new_value` are numeric (`DECIMAL(18,2)` columns, coerced via
  `float()`); `action` is exactly the string `"UPDATE"` or `"INSERT"` (T-SQL `$action`
  literal values). `PORTS[row.port_code]` and `CARGO_TYPES[row.cargo_type_code]` are
  **safe by construction here** — not a new trust boundary — because if `row.port_code`
  or `row.cargo_type_code` weren't in `PORTS`/`CARGO_TYPES`, execution would already have
  raised at L92/L93 before reaching this branch; both `port_ids`/`cargo_type_ids` and
  `PORTS`/`CARGO_TYPES` share the same keyspace since the former are built by iterating
  the latter (`upsert_ports` L49, `upsert_cargo_types` L67).
- **Establishes:** `revision_flags` accumulates one flag per genuinely-changed row;
  `action == "INSERT"` (first-ever load of that natural key) never produces a revision
  flag, which is the intended new-data path per the docstring (L82-86).
- **Depended on by:** `load_all` (L157-158), which concatenates `revision_flags` onto the
  caller-supplied `flags` before the final `upsert_flags` call.

```python
# L120-121
conn.commit()
return throughput_ids, revision_flags
```
- **Assumes:** Nothing raised earlier in the loop across all of `rows`; if a `KeyError`/
  `TypeError` strikes at row N, none of this function's MERGEs (rows 0..N) are committed
  — but `upsert_ports`/`upsert_cargo_types` already committed their own writes earlier in
  `load_all`, so a mid-function failure here leaves ports/cargo types persisted with no
  throughput rows or flags, and no rollback anywhere in this module unwinds that.

**Cross-Function Dependencies:**
- Callee `build_throughput_upsert` (internal, upsert.py L80-121): loader.py depends on
  its `ON` clause natural key matching schema.sql's `UQ_throughput_natural_key` exactly
  (both list `port_id, cargo_type_id, year, direction, source` — schema.sql L48-49,
  upsert.py L91-95) for the fetchone-always-one-row invariant to hold, and on its OUTPUT
  column order (`$action, throughput_id, deleted.gross_weight_tonnes,
  inserted.gross_weight_tonnes`, upsert.py L101-105) matching loader.py's positional
  unpack order (L96) exactly. Neither correspondence is checked by any code; both are
  maintained by hand across the two modules.
- Callers: `load_all` (L157) passes this function's two return values straight into
  `upsert_flags` (via `all_flags` and `throughput_ids`, L158-159) without re-validation.
- Shared state: `port_ids`/`cargo_type_ids` (read-only here, written by
  `upsert_ports`/`upsert_cargo_types`); `PORTS`/`CARGO_TYPES` module-level dicts
  (read-only here).

**Open Questions:**
- unclear; need to inspect `transform_all` (transform/pipeline.py) and the ingest layer
  to determine whether it's actually guaranteed that every `PortThroughputRow.port_code`/
  `cargo_type_code` reaching this function is a member of `PORTS`/`CARGO_TYPES` — nothing
  in loader.py itself establishes or checks this, and reference_data.py L35-37
  documents at least one code (`'UNK'`) that is deliberately excluded from
  `CARGO_TYPES`, implying upstream data could plausibly contain it.
- unclear; need to inspect whether `rows` can ever contain two entries with the same
  `(port_code, cargo_type_code, year, direction)` but different `source` within a single
  `load_all` call (e.g., a real Eurostat row and a `derived_sum:...` row for the same
  period) — if so, the `ThroughputKey` collision at L97-98 is reachable within normal
  operation, not just across separate runs.

---

## `upsert_flags` in src/port_analytics/load/loader.py (L124-146)

**Purpose:** Insert-only load of data-quality flags (both pipeline-detected flags passed
into `load_all` and the `REVISED_ESTIMATE` flags `upsert_throughput_rows` just produced),
resolving each flag's optional `port_code`/`throughput_ref` to surrogate FK values.

**Inputs & Assumptions:**
- `conn`: trusted, as above.
- `flags` (list[DataQualityFlag]): semi-trusted, upstream-produced (transform layer) plus
  internally-produced (`revision_flags` from `upsert_throughput_rows`).
- `port_ids` (dict[str,int]), `throughput_ids` (dict[ThroughputKey,int]): trusted,
  produced earlier in `load_all`.
- Precondition (unenforced): every non-`None` `flag.port_code` must be a key in
  `port_ids` (i.e., in `PORTS`) — same cross-module membership assumption as
  `upsert_throughput_rows`, here applied to flags instead of throughput rows.

**Outputs & Effects:**
- No return value (`None`). Inserts (never updates — MERGE has only a `WHEN NOT MATCHED`
  branch, upsert.py L144-146) each flag, committed once at L146.

**Block-by-Block:**

```python
# L131-132
for flag in flags:
    port_id = port_ids[flag.port_code] if flag.port_code else None
```
- **What:** Resolves the optional `port_id` FK, defaulting to `None` when
  `flag.port_code` is falsy (`None` or empty string — `models.py` L70 types it as
  `str | None`, so an empty string would also take the `None` branch here since it's
  falsy).
- **Assumes:** If `flag.port_code` is truthy, it's a key in `port_ids`. Not checked;
  `KeyError` propagates uncaught if violated — this is a plain indexing (`[]`), not the
  defensive `.get()` used two lines later for `throughput_ids`.
- **Establishes:** `port_id` is either a valid surrogate key or `None`, ready for
  `build_flag_upsert`.

```python
# L133-143
throughput_id = None
if flag.throughput_ref is not None:
    key: ThroughputKey = (
        flag.throughput_ref.port_code,
        flag.throughput_ref.cargo_type_code,
        flag.throughput_ref.year,
        flag.throughput_ref.direction.value,
    )
    throughput_id = throughput_ids.get(key)
```
- **What:** Builds the same 4-tuple shape as `upsert_throughput_rows` L97 from
  `flag.throughput_ref` (a `ThroughputRef`, models.py L45-52) and looks it up with
  `.get()`, defaulting to `None` on a miss.
- **Why here:** Comment (L141-142) states a miss is "the common case, since these flags
  describe missing data by definition" — i.e., a flag about a row that was never loaded
  (e.g. `MISSING_YEAR`) is expected to *not* find a `throughput_id`. This is the one
  place in the module that treats a dict miss as expected rather than an invariant
  violation.
- **Assumes:** The tuple field order here (`port_code, cargo_type_code, year,
  direction.value`) matches `ThroughputKey`'s construction in `upsert_throughput_rows`
  L97 exactly — both must stay in sync by hand; a reordering in one without the other
  would silently turn every lookup into a miss (indistinguishable from the legitimate
  "missing data" case this code path already tolerates) rather than raising.
- **Establishes:** `throughput_id` is `None` for both "genuinely no such throughput row"
  and "key shape mismatch" — these two cases are not distinguishable from inside this
  function.

```python
# L144-146
stmt = build_flag_upsert(flag, port_id, throughput_id)
cursor.execute(stmt.sql, stmt.params)
conn.commit()  # (after the loop, once)
```
- **What:** Builds and executes the insert-only MERGE; no `fetchone()` call, correctly,
  since `build_flag_upsert`'s SQL has no `OUTPUT` clause (upsert.py L133-147).
- **Assumes:** Nothing raised earlier in the loop; if a `KeyError` strikes at flag N
  (unresolved `port_code`), none of this function's flag inserts (0..N) are committed —
  same partial-pipeline shape as the other upsert functions.
- **Establishes:** All-or-nothing commit of this function's own writes only; does not
  and cannot roll back the ports/cargo-types/throughput rows already committed by earlier
  steps in `load_all`.

**Cross-Function Dependencies:**
- Callee `build_flag_upsert` (internal, upsert.py L124-159): loader.py depends on its
  NULL-safe MERGE `ON` clause (upsert.py L138-143) to make re-running with an identical
  flag a no-op (insert-only idempotency) — not verified here, trusted from the module
  docstring/comments (upsert.py L129-132).
- Callers: `load_all` (L159) is the only caller; passes `all_flags` (caller-supplied
  `flags` concatenated with `revision_flags`, L158) and the `port_ids`/`throughput_ids`
  built earlier in the same `load_all` call.
- Shared state: reads `port_ids`, `throughput_ids` (both read-only here).
- Invariant coupling: this function's correctness for `throughput_ref` resolution depends
  entirely on `upsert_throughput_rows` having populated `throughput_ids` with the same
  key shape and having run to completion (via call order in `load_all`, L157 before
  L159) — nothing re-validates that `upsert_throughput_rows` actually succeeded beyond
  Python's normal exception propagation (if it raised, `load_all` never reaches L159).

**Open Questions:**
- unclear; need to inspect whether `flag.port_code` can legitimately be an empty string
  (as opposed to `None`) from any producer of `DataQualityFlag` — if so, L132's falsy
  check (`if flag.port_code`) treats it identically to `None`, silently dropping a
  `port_id` that might otherwise have been resolvable.

---

## `load_all` in src/port_analytics/load/loader.py (L149-167)

**Purpose:** The module's single entry point (only function imported elsewhere — cli.py
L16). Orchestrates the fixed five-step sequence (schema, ports, cargo types, throughput,
flags) that the FK structure in schema.sql requires, and assembles a `LoadSummary` of
counts.

**Inputs & Assumptions:**
- `conn` (pyodbc.Connection): trusted, expected open and usable; obtained by the caller
  via `connection.py:connect()` inside a `with` block (cli.py L47-48).
- `rows` (list[PortThroughputRow]), `flags` (list[DataQualityFlag]): semi-trusted,
  produced by `transform_all` upstream (cli.py L44).
- Implicit: reads module-level `PORTS`, `CARGO_TYPES` (via the functions it calls).
- Precondition: none stated or enforced beyond call order — this function is the
  *source* of the ordering invariant, not a consumer of one.

**Outputs & Effects:**
- Returns `LoadSummary` (L31-36, a `NamedTuple`) with five counts.
- Full side effect: schema applied, then ports/cargo-types/throughput/flags all
  upserted and committed to `conn`'s database, in four separate transactions (one commit
  per called function, not one transaction for the whole pipeline).

**Block-by-Block:**

```python
# L154-159
apply_schema(conn)
port_ids = upsert_ports(conn)
cargo_type_ids = upsert_cargo_types(conn)
throughput_ids, revision_flags = upsert_throughput_rows(conn, rows, port_ids, cargo_type_ids)
all_flags = flags + revision_flags
upsert_flags(conn, all_flags, port_ids, throughput_ids)
```
- **What:** The fixed pipeline order.
- **Why here:** This order is exactly what schema.sql's FK constraints require: ports and
  cargo types must exist before `port_throughput` rows can reference them
  (`FK_throughput_port`/`FK_throughput_cargo_type`, schema.sql L42-45), and
  `port_throughput` rows must exist before flags can reference them via `throughput_id`
  (`FK_flags_throughput`, schema.sql L63-64). Reordering any two of these calls would
  produce FK violations at the database level for any row/flag needing the not-yet-loaded
  reference.
- **Assumes:** Each call either fully succeeds (and commits its own work) or raises. No
  step re-checks that a prior step actually produced usable output beyond receiving its
  return value.
- **Establishes:** If `load_all` returns normally, all five steps completed and each
  committed independently — but see below: this is *not* one atomic operation.
- **Depended on by:** `cli.py` (L48), which reports the returned `LoadSummary` to the
  user (L50+) as the pipeline's result.

```python
# L161-167
return LoadSummary(
    ports_loaded=len(port_ids),
    cargo_types_loaded=len(cargo_type_ids),
    throughput_rows_loaded=len(rows),
    flags_loaded=len(all_flags),
    revisions_detected=len(revision_flags),
)
```
- **What:** Builds the summary purely from lengths of already-computed collections.
- **Assumes:** Nothing new.
- **Establishes:** `throughput_rows_loaded` reports `len(rows)` — the count of *input*
  rows passed to `upsert_throughput_rows`, not `len(throughput_ids)` (the count of
  distinct natural keys actually written). If `rows` contained a `ThroughputKey`
  collision (see `upsert_throughput_rows`'s Open Questions — same `(port_code,
  cargo_type_code, year, direction)` but different `source`), both rows are counted in
  `throughput_rows_loaded` even though `throughput_ids` retained only the later one's
  `throughput_id` under the shared key. This is a structural fact about what the summary
  measures, not a claim about whether such a collision is reachable.

**Cross-Function Dependencies:**
- Callees: `apply_schema`, `upsert_ports`, `upsert_cargo_types`, `upsert_throughput_rows`,
  `upsert_flags` — all internal, analyzed above. `load_all` depends on each to commit its
  own work and to raise (rather than silently swallow) on failure, since `load_all` itself
  has no try/except anywhere in its body (L149-167).
- Callers: `cli.py` L47-48, inside `with connect() as conn: summary = load_all(conn, rows,
  flags)`. The caller depends on `pyodbc.Connection`'s context-manager protocol to do
  something sensible (commit/rollback/close) when `load_all` raises inside the `with`
  block — `pyodbc` is a C extension, its `__exit__` behavior is not verified from source
  here (see Open Questions).
- Invariant coupling: no step in `load_all` is wrapped in a savepoint, nested transaction,
  or try/except with rollback. The five `conn.commit()` calls spread across
  `apply_schema`/`upsert_ports`/`upsert_cargo_types`/`upsert_throughput_rows`/
  `upsert_flags` mean a failure partway through `load_all` (e.g., an unrecognized
  `port_code` in `rows` at the `upsert_throughput_rows` stage) leaves the database with
  schema + ports + cargo types committed, but no throughput rows or flags from that call —
  a partially-applied pipeline state, with nothing in this module to detect, report, or
  clean that up on a subsequent run (the next run's MERGEs would simply pick up where the
  failed one left off, since everything is idempotent per-row, but `load_all` itself gives
  no signal that the previous run was partial beyond the exception it raised).

**Open Questions:**
- unclear; need to inspect `pyodbc.Connection.__exit__` (C extension, not Python source)
  to determine whether an exception raised inside `with connect() as conn:` (cli.py
  L47-48) — e.g. propagating from `load_all` — triggers an implicit rollback of whatever
  is uncommitted, or leaves the connection's transaction state undefined until garbage
  collection/close. This matters because `load_all` performs no rollback of its own.
- unclear; need to inspect `connection.py:connect()`/`build_connection_string()` for
  whether `pyodbc.connect()` (connection.py L55) is called with any autocommit setting —
  none is passed explicitly (connection.py L54-55), so this analysis assumes pyodbc's
  default (`autocommit=False`), which is what makes every `conn.commit()` call in
  loader.py necessary; not independently verified from pyodbc source.
