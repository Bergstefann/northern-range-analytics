# Northern Range Port Analytics

Pipeline turning real Eurostat maritime statistics into Northern Range port throughput
analytics. Work in progress — architecture diagrams, DAX measures, and Power BI screenshots
land at the end of the build per `docs/data-project-build-spec.md`. This section is written
incrementally as real issues are found, not backfilled at the end.

## Data quality

### The Antwerp continuity decision

Eurostat never published a unified "Antwerp-Bruges" figure before the 2022 merger.
Antwerpen and Zeebrugge reported separately through 2021; the merged entity reports from
2022, with a clean cutover and no overlap year.

**Decision: keep both views, explicitly.** The raw Antwerpen and Zeebrugge rows are kept
untouched under their own port codes. In addition, a pre-2022 Antwerp-Bruges series is
derived by summing the two legacy ports for every year/cargo-type/direction where both
reported a value, stored under `source='derived_sum:BE_0BEANR+BE_0BEZEE'` so it's never
mistaken for a real Eurostat figure. Every derived row is backed by a `data_quality_flags`
row (`port_merger`) that states this plainly.

**The summation is imperfect, and that's on the record, not hidden:**
- It assumes Antwerpen's and Zeebrugge's pre-2022 reporting methodologies were compatible
  with each other and with the post-2022 unified authority's — unverifiable, since
  Eurostat never published an independent figure to check against.
- Any intra-complex traffic both ports separately counted as "goods handled" would be
  double-counted in the sum (unlikely for seaborne cargo stats specifically, not ruled
  out).
- A future Eurostat revision to the legacy figures would need to be manually re-summed —
  the derived rows have no independent source of their own.

Full reasoning: `docs/data-quality-notes.md`, "Antwerp continuity decision."

### A flag type was removed, not just left unused

The build spec originally listed `suppressed_confidential` as a `data_quality_flags` type.
It's been dropped. Eurostat's JSON-stat API exposes no confidentiality flag at all — a
missing (port, year, cargo) value is indistinguishable between "confidential", "not
collected", and "not applicable." This pipeline can reliably detect *that* a value is
missing; it cannot detect *why*. An always-empty flag type that can never be honestly
populated would misrepresent a capability the pipeline doesn't have, so it's removed rather
than kept as dead schema. **If Eurostat is silently suppressing a confidential figure for
one of these ports, this pipeline cannot tell that apart from the figure simply not
existing.** Every observed gap is instead classified as `missing_year` (plain absence) or
`code_change` (a clean start/stop pattern — see below).

### Findings from the real data

- **Dataset codes in the original plan were wrong.** `mar_mg_am_pwhd` and `mar_go_am`
  don't exist. The real codes are `mar_mg_aa_pwhd` (goods by direction) and
  `mar_mg_am_pwhc` (goods by cargo type) — confirmed against the live API, not from search
  results.
- **Hamburg's non-self-propelled Ro-Ro reporting stops cleanly after 2011.** Small volumes
  every year 2005–2011, zero rows 2012–2024. A contiguous cutoff rather than scattered
  gaps, so it's flagged `code_change` (a likely reporting or classification change) rather
  than thirteen individual `missing_year` rows.
- **A phantom cargo category.** The cargo dataset defines an `UNK` ("Unknown") category
  that has zero data points for every port and every year. Excluded from the `cargo_types`
  table entirely rather than flagged — there's no row to attach a flag to.
- **Units are consistent** between the two datasets actually used (both report tonnage in
  `THS_T`), though a related but unused Eurostat dataset in this family mixes units,
  keeping `unit_mismatch` a live concern for any future addition to this pipeline.
- **A real unit bug, caught before it reached the database.** Eurostat reports `THS_T`
  (thousand tonnes); the transform was initially passing that raw value straight into a
  field named `gross_weight_tonnes` — a silent 1000x understatement. Building the Azure SQL
  loader forced the check (the column name is `gross_weight_tonnes`, not
  `gross_weight_thousand_tonnes`), and the conversion is now applied once, at the raw ->
  domain boundary, not in the loader.

Full investigation notes, including the JSON-stat index math and every design decision
behind the transform: `docs/data-quality-notes.md`.

### Numbers, verified against the real run

1,013 `port_throughput` rows, 4 `data_quality_flags` rows (1 `code_change`, 3
`port_merger`) — from the real Eurostat landing of `mar_mg_aa_pwhd` and `mar_mg_am_pwhc`
for the five target ports, transformed and loaded into the live Azure SQL database. Four
flags against a thousand rows is deliberate: every flag traces to a verified pattern in the
data, not a speculative one. Confirmed idempotent by running the full pipeline twice in a
row and checking row counts directly in the database — identical both times, 0 duplicate
rows, 0 false-positive revisions.

## Running the pipeline

```
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -e . ruff mypy pytest pytest-cov types-requests
cp .env.example .env   # fill in the real Azure SQL credentials
port-analytics
```

Requires the [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
installed locally (`pyodbc` links against it). CI installs `unixodbc` on Linux for the same
reason.

## Infrastructure

Azure SQL Database, free tier, **$0/month**. `rg-northern-range-analytics` /
`northern-range-sql-server` in `australiaeast` — a new resource group and server, kept
separate from PortYard's `rg-portyard` for clean cost tracking and teardown (a logical SQL
server carries no cost of its own, so this costs nothing extra). Created with the
`AutoPause` free-limit-exhaustion behavior: exceeding the monthly free allowance (100,000
vCore-seconds / 32 GB) pauses the database until next month rather than billing. Full
reasoning, including why a second free database is available on this subscription at all:
`docs/data-quality-notes.md`, "Phase 3 — Azure resource decisions."
