# Database schema (`schema.sql`)

## `schema.sql` (DDL) in src/port_analytics/load/schema.sql (L1-68)

**Purpose:** Defines the four Azure SQL tables (`ports`, `cargo_types`, `port_throughput`,
`data_quality_flags`) that the loader (`load/loader.py`) and the MERGE statements
(`load/upsert.py`) target. It is applied once per process via `apply_schema()`
(`load/loader.py` L39-43), guarded by `IF OBJECT_ID(...) IS NULL` blocks (L6, L21, L31, L53)
so the whole script is safe to re-run against an already-provisioned database (comment
L2-4). Without it, none of the MERGE statements in `upsert.py` have a target, and — more
specifically — several of them have no unique index to MERGE against, which is what keeps
the pipeline idempotent per the natural-key comment at L46-47.

**Inputs & Assumptions:**
- No parameters; it is static DDL text read from disk (`loader.py` L40: `SCHEMA_PATH.read_text(...)`) and executed as one batch (`loader.py` L42: `cursor.execute(sql)`).
- Implicit: a live `pyodbc.Connection` with DDL privilege on the target database (supplied by `connection.py` L54-55, built from env vars `connection.py` L15-20). Trust: trusted (comes from the app's own config, not user input).
- Precondition the script's own comment asserts: re-running it is a no-op once the four tables exist (L2-4). This holds for table *creation* only — each `IF OBJECT_ID(...) IS NULL BEGIN CREATE TABLE ... END` block (L6-19, L21-29, L31-51, L53-68) skips table creation if the object already exists, but there is no `ALTER TABLE` path. If a later revision of this file changed a column definition or added a constraint, an already-provisioned database would silently keep the old shape — nothing in the script detects or reconciles drift. Established by: nothing; the idempotency comment (L2-4) covers existence, not shape.

**Outputs & Effects:**
- DDL effects only: creates up to four tables with their columns, PKs, FKs, and UNIQUE constraints, each conditionally (L6-68). No rows are written.
- Establishes the constraint surface every `upsert.py` MERGE/UPDATE statement and every `loader.py` caller structurally depends on (detailed below).

**Block-by-Block:**

```sql
-- L8-18
CREATE TABLE dbo.ports (
    port_id              INT IDENTITY(1,1) PRIMARY KEY,
    port_name            NVARCHAR(100)  NOT NULL,
    country_code         CHAR(2)        NOT NULL,
    un_locode            CHAR(5)        NULL,
    eurostat_code        NVARCHAR(20)   NOT NULL,
    merged_into_port_id  INT            NULL,
    CONSTRAINT UQ_ports_eurostat_code UNIQUE (eurostat_code),
    CONSTRAINT FK_ports_merged_into FOREIGN KEY (merged_into_port_id)
        REFERENCES dbo.ports (port_id)
);
```
- **What:** `ports` table: surrogate PK, a unique natural key (`eurostat_code`), and a nullable self-referencing FK for port mergers (e.g. Antwerp/Zeebrugge → Antwerp-Bruges, per `docs/data-project-build-spec.md` L128).
- **Why here:** created first because `port_throughput` and `data_quality_flags` both FK into it (L42-43, L65-66).
- **Assumes:** `country_code` values fit CHAR(2) and `un_locode` values fit CHAR(5) — nothing in the Python model enforces this before insert (`models.py` L31-37: `Port.country_code: str`, `Port.un_locode: str | None` have no length/pattern validators). Reference data (`transform/reference_data.py` L10-27) happens to always supply 2-letter codes and never sets `un_locode`, so the constraint is never exercised today.
- **Establishes:** `UQ_ports_eurostat_code` (L15) is the invariant `build_port_upsert`'s `MERGE ... ON target.eurostat_code = src.eurostat_code` (`upsert.py` L33) depends on to match at most one row — see Cross-Function Dependencies.
- **Depended on by:** `build_port_merge_link` (`upsert.py` L54-56), which does a plain `UPDATE ... WHERE eurostat_code = ?` and relies on the same uniqueness to update exactly one row; `FK_throughput_port` (L42-43) and `FK_flags_port` (L65-66), which depend on every `port_id` referenced elsewhere actually existing in `ports`.
- **Note:** no `CHECK` prevents `merged_into_port_id = port_id` (self-merge) or a merge cycle (A→B→A). Nothing in `loader.py`/`upsert.py` checks this either — `upsert_ports` (`loader.py` L46-61) just applies whatever `Port.merged_into` says (L55-58) with no cycle detection. Not exercised by current reference data (`reference_data.py` L10-27: at most one hop), so it's an unenforced assumption rather than a demonstrated gap.

```sql
-- L33-51
CREATE TABLE dbo.port_throughput (
    ...
    CONSTRAINT FK_throughput_port FOREIGN KEY (port_id)
        REFERENCES dbo.ports (port_id),
    CONSTRAINT FK_throughput_cargo_type FOREIGN KEY (cargo_type_id)
        REFERENCES dbo.cargo_types (cargo_type_id),
    -- Natural key: what makes a re-run idempotent (Phase 3 loader
    -- MERGEs on this instead of duplicating rows).
    CONSTRAINT UQ_throughput_natural_key UNIQUE
        (port_id, cargo_type_id, year, direction, source)
);
```
- **What:** fact table for tonnage figures; FKs to `ports`/`cargo_types`, plus a five-column UNIQUE natural key.
- **Assumes:** `direction` (NVARCHAR(10) NOT NULL, L38) holds one of `'total'/'inbound'/'outbound'`. The schema has no `CHECK` constraint restricting the value set — that restriction lives entirely in Python's `Direction` `StrEnum` (`models.py` L13-16), enforced by pydantic validation when a `PortThroughputRow` is constructed. `gross_weight_tonnes` (DECIMAL(18,2) NOT NULL, L39) similarly has no `CHECK (>= 0)`; `PortThroughputRow.gross_weight_tonnes` is a bare `float` with no validator (`models.py` L60) — nothing at either layer forbids a negative tonnage value from being stored.
- **Establishes:** `UQ_throughput_natural_key` (L48-49) is exactly the five columns `build_throughput_upsert`'s `MERGE ... ON` clause matches on (`upsert.py` L91-95: `port_id, cargo_type_id, year, direction, source`) — the comment at L46-47 states this explicitly and the two column lists line up 1:1.
- **Depended on by:** `upsert_throughput_rows` (`loader.py` L91-121), which calls `cursor.fetchone()` once per row (L96) and unpacks exactly four columns (`action, throughput_id, old_value, new_value`) — this only works if the MERGE's ON-predicate matches at most one target row, which the UNIQUE constraint (L48-49) guarantees; without it a duplicate natural key in `port_throughput` could make the MERGE match >1 row and change `OUTPUT`'s row count.
- **Depended on by (data quality logic):** `ingested_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()` (L41) is only assigned by the `INSERT` branch of the MERGE; the `WHEN MATCHED THEN UPDATE SET gross_weight_tonnes = ?` branch (`upsert.py` L96-97) never touches `ingested_at`. So `ingested_at` reflects first-load time, not last-modified time, even when a value is later revised — this is what lets `upsert_throughput_rows` (`loader.py` L100-118) distinguish "old_value" (pre-update) from "new_value" via the `OUTPUT deleted.gross_weight_tonnes, inserted.gross_weight_tonnes` clause (`upsert.py` L104-105), but it also means the table's own timestamp column cannot be used downstream to detect that a revision happened — that fact only exists transiently in the `revised_estimate` flag row produced by `loader.py` L104-118.

```sql
-- L55-68
CREATE TABLE dbo.data_quality_flags (
    flag_id        INT IDENTITY(1,1) PRIMARY KEY,
    throughput_id   INT             NULL,
    port_id          INT             NULL,
    flag_type         NVARCHAR(30)    NOT NULL,
    description         NVARCHAR(1000)  NOT NULL,
    resolution           NVARCHAR(1000)  NOT NULL,
    created_at             DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_flags_throughput FOREIGN KEY (throughput_id)
        REFERENCES dbo.port_throughput (throughput_id),
    CONSTRAINT FK_flags_port FOREIGN KEY (port_id)
        REFERENCES dbo.ports (port_id)
);
```
- **What:** flags table; both FKs are nullable (matches spec doc `docs/data-project-build-spec.md` L146-147: "some flags are about missing rows").
- **Assumes:** `flag_type` (NVARCHAR(30)) is restricted to the six values in Python's `FlagType` `StrEnum` (`models.py` L19-28) — again no DB-level `CHECK`, enforcement is pydantic-only at construction of `DataQualityFlag`.
- **Establishes:** nothing that guarantees uniqueness — **this table has no `UNIQUE` constraint of any kind beyond the surrogate `flag_id` PK.** Compare to `ports` (L15), `cargo_types` (L27), and `port_throughput` (L48-49), each of which has an explicit natural-key `UNIQUE` constraint that its corresponding `upsert.py` MERGE statement matches against.
- **Cross-reference gap:** `build_flag_upsert` (`upsert.py` L124-159) is written exactly like the other three MERGE builders — it MERGEs `ON` a four-column composite `(throughput_id, port_id, flag_type, description)` with explicit NULL-safe equality (`upsert.py` L138-143) and the docstring calls this the thing it "MERGEs on" (L130-131), treating it as a natural key in the same idiom as `UQ_throughput_natural_key`. But `schema.sql` never declares `(throughput_id, port_id, flag_type, description)` — or any subset — as `UNIQUE`. The MERGE's `ON`-predicate is evaluated per-statement against whatever rows currently exist; nothing at the schema level prevents two rows with an identical `(throughput_id, port_id, flag_type, description)` tuple from coexisting, and nothing prevents two concurrent MERGE statements from both seeing `WHEN NOT MATCHED` and both inserting. The other three tables close this gap with a real unique index (L15, L27, L48-49); `data_quality_flags` does not. Established by: nothing found in `schema.sql`.
- **Depended on by:** `upsert_flags` (`loader.py` L124-146), which loops over flags and calls `build_flag_upsert` per flag (L144) without reading any output (no `OUTPUT` clause exists in the flag SQL, `upsert.py` L133-147) and without checking row-count or duplicate outcome — it has no way to observe whether the MERGE actually deduplicated.

**Cross-Function Dependencies:**
- Callee `apply_schema` (`load/loader.py` L39-43, internal): this DDL is what `apply_schema` executes verbatim (L40-42) inside `load_all` (`loader.py` L149-167, L154) before any upsert runs. `apply_schema` does no error handling around `cursor.execute(sql)` (L42) — if any `CREATE TABLE` block fails partway (e.g. permissions, a concurrent conflicting DDL change), the exception propagates uncaught to `load_all`'s caller; there is no per-table rollback distinct from the single `conn.commit()` at L43.
- Callee `build_port_upsert` / `build_port_merge_link` / `build_cargo_type_upsert` / `build_throughput_upsert` / `build_flag_upsert` (`load/upsert.py`, internal): each is a pure SQL-text builder (no DB access, per module docstring L1-11) whose correctness as an *idempotent* statement depends entirely on the schema declaring the same columns the builder's `ON` clause uses as `UNIQUE`. This holds for ports (L15 vs `upsert.py` L33), cargo_types (L27 vs `upsert.py` L63), and port_throughput (L48-49 vs `upsert.py` L91-95); it does **not** hold for data_quality_flags (no constraint vs `upsert.py` L138-143), per the gap above.
- Callee `connection.py::connect` (internal, not part of this file but supplies its input): builds the `pyodbc.Connection` `apply_schema` executes against (`connection.py` L54-55). Failure mode (missing env vars) is a `MissingConnectionConfig` raised before any connection exists (`connection.py` L31-35) — `schema.sql` is never reached in that path, so it never partially applies due to config errors.
- Callers: `load_all` (`loader.py` L149-167) is the only caller of `apply_schema`, and therefore the only path that applies this schema. It assumes (by ordering — `apply_schema(conn)` at L154 runs before any `upsert_*` call at L155-159) that all four tables and their constraints exist before any MERGE executes; there is no explicit check of this beyond execution order.
- Invariant couplings: the five-column `UQ_throughput_natural_key` (L48-49) is also read by `docs/data-project-build-spec.md` §4 only informally (no explicit uniqueness spec there, L134-142) — the uniqueness requirement is stated solely in the schema's own inline comment (L46-47) and mirrored structurally in `upsert.py`'s `ON` clause. The `data_quality_flags` FKs being nullable (L57-58, L65-66) directly reflects `loader.py`'s `upsert_flags` (L131-143), which passes `None` for `port_id`/`throughput_id` when a flag has no `port_code` (L132) or no matching `throughput_ref` (L133-143, notably L143: `.get(key)` silently returns `None` if the referenced throughput row wasn't loaded — "the common case" per the comment at L141-142).

**Open Questions:**
- Whether SQL Server, under this database's actual session/ANSI settings, truncates or hard-errors when an oversized string is inserted into a fixed-capacity column (`port_name` NVARCHAR(100) L10, `country_code`/`un_locode` CHAR(2)/CHAR(5) L11-12, `eurostat_code` NVARCHAR(20) L13, `source` NVARCHAR(50) L40, `flag_type` NVARCHAR(30) L59, `description`/`resolution` NVARCHAR(1000) L60-61) — none of these are length-validated in `models.py` before being passed as MERGE parameters (`upsert.py` throughout), so the schema's column widths are the only backstop; unclear whether that backstop fails loudly or silently truncates under this project's connection settings — need to inspect the actual Azure SQL database's `SET ANSI_WARNINGS`/compatibility level, not visible from this file.
- Whether any migration path exists elsewhere in the repo for altering an already-created table (the `IF OBJECT_ID(...) IS NULL` guards at L6, L21, L31, L53 only ever `CREATE`, never `ALTER`) — need to check for a separate migrations mechanism outside `load/schema.sql` and `load/loader.py`.
- Whether concurrent/parallel invocations of `load_all` (or of `upsert_flags` specifically) are a real deployment scenario for this pipeline — the absence of a unique constraint on `data_quality_flags`' natural-key tuple only matters structurally if more than one writer can race; nothing in `loader.py` or the README (not read in this pass) indicates whether the pipeline is ever run concurrently.
