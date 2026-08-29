# Data quality notes — Phase 1, 2 & 3

Working notes from the live-API investigation, the transform build, and the Azure SQL load,
all on 2026-08-19. This is the durable record of what was found and decided.
`data_quality_flags` rows populated by the pipeline trace back to the findings here rather
than rediscovering them.

## Dataset codes

The build spec originally listed `mar_mg_am_pwhd` and `mar_go_am`. Both 404 against the live
API. They don't exist. Verified directly against
`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{code}?format=JSON&lang=EN`,
not from search snippets alone (an initial search-engine summary suggested other plausible-looking
but also-wrong codes, e.g. `mar_mg_am_cwhc`, `mar_mg_aa_cwh`; the latter is real but is a
country-level aggregate, not per-port, so it's the wrong granularity for this project).

The real codes, confirmed by fetching each and inspecting `dimension.rep_mar.category`:

| Purpose | Code | Title | Dimensions |
|---|---|---|---|
| Goods by direction | `mar_mg_aa_pwhd` | Gross weight of goods handled in the top 20 EU ports by direction | `freq, unit, direct, rep_mar, time` |
| Goods by cargo type | `mar_mg_am_pwhc` | Gross weight of goods handled in the top 20 EU main ports by type of cargo | `freq, unit, cargo, rep_mar, time` |

Both cover **20 years, 2005–2024**, for our 6 `rep_mar` codes (see below), well past the
spec's 10-year target. `docs/data-project-build-spec.md` §2 has been corrected in place.

Note the inconsistent infix: `mar_mg_aa_pwhd` (`aa`) vs `mar_mg_am_pwhc` (`am`). Not a typo
on our part. That's genuinely how Eurostat named these two related datasets.

## Port codes (`rep_mar` dimension)

All 5 target ports are covered, but Antwerp requires 3 codes because of the 2022 merger:

| Code | Name | Notes |
|---|---|---|
| `BE_0BE003` | Antwerp-Bruges | Merged entity, reports from 2022 |
| `BE_0BEANR` | Antwerpen | Legacy pre-merger code, reports through 2021 |
| `BE_0BEZEE` | Zeebrugge | Legacy pre-merger code, reports through 2021 |
| `DE_1DEHAM` | Hamburg | Full 2005–2024 coverage |
| `NL_0NLRTM` | Rotterdam | Full 2005–2024 coverage |
| `PL_0PLGDN` | Gdansk | Full 2005–2024 coverage |

## Finding 1 — port merger breaks the Antwerp-Bruges time series (`port_merger`)

Confirmed directly, not inferred, by checking per-year presence of `BE_0BE003` /
`BE_0BEANR` / `BE_0BEZEE` in the `value` map:

- `BE_0BEANR` and `BE_0BEZEE`: present every year 2005–2021, absent every year 2022–2024.
- `BE_0BE003`: absent every year 2005–2021, present every year 2022–2024.

The cutover is **clean**. No overlap year where both the legacy and merged codes report
(good: no double-counting risk), but also no continuous "Antwerp-Bruges" series exists as a
single Eurostat identifier. Pre-2022 Antwerp-Bruges totals must be computed by summing
`BE_0BEANR + BE_0BEZEE`. This is exactly the `port_merger` flag scenario in the schema.
Phase 2 needs a transform step that builds the combined pre-2022 series and flags each
synthesized row with `port_merger`, pointing at `merged_into_port_id` on the `ports` table.

Sanity check on magnitude: Antwerpen 2021 (215,852) + Zeebrugge 2021 (40,130) = 255,982
thousand tonnes, vs. Antwerp-Bruges 2022 (254,257). Consistent scale, which supports treating
these as the same underlying port complex across the cutover.

## Antwerp continuity decision (Phase 2) — the most important decision in this project

Three options were on the table for pre-2022 Antwerp-Bruges: sum the legacy codes, keep
them separate, or both. **Decision: both, explicitly, with every derived row flagged.**
Implemented in `src/port_analytics/transform/continuity.py`.

- The raw per-legacy-port rows (`BE_0BEANR`, `BE_0BEZEE`) are always kept untouched, under
  their own port codes, with `source` set to whichever real Eurostat dataset they came
  from. Nothing about them is altered.
- **In addition**, for every (cargo_type, direction, year) before 2022 where *both* legacy
  ports reported a value, a derived Antwerp-Bruges row is computed by summing them and
  stored under `BE_0BE003` with `source='derived_sum:BE_0BEANR+BE_0BEZEE'`, a source value
  that can never be confused with a real Eurostat dataset code.
- If only one legacy port has a value for a given combination, **no derived row is
  created**. Summing one real number and one absence would silently understate the total,
  which is worse than leaving it absent. That case gets its own `missing_year` flag
  instead. In the real data this never happens, since both legacy ports are fully populated
  for every year they're expected to report, but the transform doesn't assume that.

Why not just pick one? Keeping legacy-only would mean no query against
`port_id=Antwerp-Bruges` ever returns anything before 2022, which is awkward for the exact
continuity analysis (YoY growth, 3-year rolling average) the project exists to do. Summing
silently would hide that the pre-2022 figure is not something Eurostat ever published.
Doing both and flagging the derived rows keeps both needs honest.

**The summing is imperfect, and this is stated on the flag row itself, not just here:**
1. It assumes Antwerpen's and Zeebrugge's pre-2022 reporting methodologies were mutually
   compatible, and compatible with the post-2022 unified authority's methodology. This
   can't be checked. Eurostat never published an independent pre-2022 unified figure to
   compare against.
2. Any intra-complex traffic that both ports separately counted as "goods handled" would
   be double-counted in the sum. Unlikely for seaborne cargo statistics specifically, but
   not ruled out.
3. Future Eurostat revisions to the legacy `BE_0BEANR`/`BE_0BEZEE` figures would need to be
   manually re-summed. The derived rows have no independent source of their own to be
   revised against.

Every derived row's existence is backed by a `data_quality_flags` row (`port_merger`,
`port_id=BE_0BE003`) carrying this exact caveat text, plus one `port_merger` flag each on
`BE_0BEANR` and `BE_0BEZEE` recording that they merged. Three flags total, not one per
derived row (2005–2021 × ~9 cargo/direction combinations would be ~150 near-identical
rows). The flags table stays high-signal instead of restating the same fact 150 times.

Any Power BI measure that wants a continuous Antwerp-Bruges series (the headline "Antwerp's
share of Northern Range volume" measure, Phase 4) must query `port_id=BE_0BE003` and will
transparently get the derived rows pre-2022 and the real rows post-2022. The `source`
column is what lets a consumer tell them apart if they need to.

## Finding 2 — phantom `UNK` cargo category (`outlier_suspected` or a new flag type)

The cargo dataset's `cargo` dimension includes a category `UNK` ("Unknown"), but it has
**zero data points across every port and every year**, confirmed via the API's own
`extension["positions-with-no-data"]` field, which lists `cargo` position 7 (`UNK`) as
entirely empty, not just missing for our 6 ports. This is a whole-category gap baked into
the dataset itself, not a per-row transform issue.

**Decision (Phase 2):** `UNK` is excluded entirely from `CARGO_TYPES`
(`transform/reference_data.py`). No `cargo_types` row is created for it, and the
transform's cargo-code allowlist means it's silently skipped if it ever appeared. No
`data_quality_flags` row either: there's no missing row to point at, since the category
never produces rows for anyone. Documented here instead.

## Finding 3 — Hamburg's `RO_MNSP` reporting stops cleanly after 2011 (`code_change`)

**Corrected from the original Phase 1 note**, which only sampled 2019+ and concluded
Hamburg had zero `RO_MNSP` (non-self-propelled Ro-Ro) rows for all 20 years. The full
2005–2024 range tells a more precise story: Hamburg reported small `RO_MNSP` volumes every
year 2005–2011 (single-digit thousand tonnes), then has zero rows for every year
2012–2024. A clean one-directional cutoff, not scattered non-reporting.

The transform (`_gap_flags` in `throughput.py`) detects this generically. A gap that's a
contiguous block at the start or end of a port's eligible year range gets flagged once as
`code_change` rather than as thirteen individual `missing_year` rows, because that pattern
(present, then never again) looks like a reporting or classification change, not random
non-reporting. The actual cause still can't be confirmed from the API alone
(reclassification into another category vs. genuine cessation of the traffic type), and the
flag says so rather than guessing.

## Finding 4 — no confidentiality flag is exposed in this API format

Eurostat's JSON-stat 2.0 response has no `status` key (checked: not present at top level,
not present per-dimension, not present per-value). The only flag-like signal is
`extension["positions-with-no-data"]`, which only reports whole-category emptiness (see
Finding 2), not per-cell suppression reasons. This means: **a missing (port, year, cargo)
combination is indistinguishable between "confidential", "not collected", and "not
applicable" using this API.** We can reliably detect *that* a value is missing (absence
from the sparse `value` map, which is how `fetch_dataset`/Phase 2 parsing works), but not
*why*. Getting the actual confidentiality reason would require Eurostat's bulk TSV/SDMX
downloads (which do carry footnote flags like `c` for confidential) or manual
cross-reference against metadata pages. Both are out of scope per the spec's "no third data
source" / time-box rules.

**Decision (Phase 2): `suppressed_confidential` is dropped from the `flag_type` enum
entirely**, not just used cautiously. An always-empty flag type that can never be honestly
populated from this API is worse than not having it. It would sit in the schema implying
a capability ("we detect confidential suppression") the pipeline doesn't actually have.
Every observed gap is instead classified as `missing_year` (plain absence, no explanation
available) or `code_change` (a clean start/stop pattern suggesting a reporting change, see
Finding 3). `docs/data-project-build-spec.md` §4 has been updated to reflect this.
This is a real, permanent limitation of the data source, not a shortcut: **if Eurostat is
silently suppressing a confidential figure for one of these ports, this pipeline cannot
tell that apart from the figure simply not existing.** Documented here and in the README's
Data quality section so it isn't lost.

## Finding 5 — units are consistent between the two datasets we use, but not universally

Both target datasets report the tonnage figure in `THS_T` (thousand tonnes):
- `mar_mg_aa_pwhd` units: `THS_T`, `RT_PRE` (growth rate on previous period)
- `mar_mg_am_pwhc` units: `THS_T`, `PC_TOT` (percentage of total)

No mismatch between the two datasets we're actually ingesting. However, the country-level
dataset `mar_mg_aa_cwh` (not used, wrong granularity, see above) additionally has a
`T_HAB` (tonnes per capita) unit, so `unit_mismatch` is a real risk in this dataset family
generally. Worth keeping the flag type even though it isn't triggered by our two datasets
today, in case a future pull touches a dataset with mixed units.

Unlike `suppressed_confidential` (dropped, see Finding 4), `unit_mismatch` and
`revised_estimate` stay in the `flag_type` enum despite zero occurrences in this run. The
distinction: those two are things this pipeline *can* detect in principle. The transform
literally filters on `unit == "THS_T"` (so a real mismatch would be visible in the
filtered-out data) and a second pull compared against the first would surface revisions.
They just haven't occurred yet. `suppressed_confidential` is something the API structurally
cannot reveal even in principle, on any pull, ever. Zero-occurrence-but-detectable is kept;
impossible-to-detect is removed.

## Value encoding, for whoever writes the Phase 2 parser

`value` is a **sparse dict** keyed by a flat, row-major index string. With `id` giving
dimension order and `size` giving each dimension's category count, the last dimension in
`id` (`time`) varies fastest. Strides are computed as
`stride[i] = product(size[i+1:])`, and the flat index for a set of per-dimension category
positions is `sum(position[i] * stride[i] for i in dims)`. A key's absence from `value`
means missing data, not zero. There is no explicit null. I verified this by hand in Python
against real API responses. A first attempt at doing this arithmetic via an LLM web-fetch
summary got the stride order backwards and produced a wrong port/year table, so don't trust
a summarized description of the index math. Decode it directly.

## Phase 2 design decisions

**A synthetic "Total" cargo type was added** (`cargo_type_code='TOTAL'`,
`transform/reference_data.py`) beyond the five example types the spec listed (containers,
dry bulk, liquid bulk, ro-ro, other). It's needed because `mar_mg_aa_pwhd` (the direction
dataset) has no cargo breakdown at all. Every row it produces represents all cargo
combined, and `cargo_type_id` needs *something* to point at. A nullable `cargo_type_id`
was the alternative and was rejected: the spec marks only `merged_into_port_id` as
nullable, and a required FK keeps downstream queries simpler (no `NULL` handling in every
cargo-type aggregation).

**`mar_mg_am_pwhc`'s own internal `cargo='TOTAL'` category is not loaded** into
`port_throughput`. Only its six real breakdown categories (liquid bulk, dry bulk,
containers, both Ro-Ro types, other) are. `mar_mg_aa_pwhd` is treated as the sole source of
`cargo_type_code='TOTAL'` rows. The alternative, loading both datasets' totals distinguished
by `source`, was rejected to avoid two independently-sourced "total" figures for the same
port/year sitting in the table with no reconciliation logic to explain any difference
between them. Simpler to have one authoritative total source and skip the redundant one, at
the cost of not cross-validating the two datasets' totals against each other. A sanity
check by hand (Finding 1) shows the two datasets agree closely (255,982 summed vs. 254,257
reported) where they can be compared, so this isn't hiding a known discrepancy.

**Real transform output**, run against the actual Phase 1 landing
(`data/raw/mar_mg_aa_pwhd_20260819T035751Z.json`,
`data/raw/mar_mg_am_pwhc_20260819T035751Z.json`): **1,013 `port_throughput` rows, 4
`data_quality_flags` rows**, being 1 `code_change` (Hamburg `RO_MNSP`, Finding 3) and 3
`port_merger` (Antwerpen, Zeebrugge, and the derived-series caveat). Four flags for over a
thousand rows is the "cheapest, highest-signal part of the project" the spec asks for, not
a token gesture. Every flag corresponds to a real, verified pattern in the data, not a
speculative one.

## Phase 3 — a real unit bug, caught before it reached the database

While building the loader, the schema's `gross_weight_tonnes` column name forced a check
that Phase 2's transform hadn't actually done. Eurostat reports `THS_T` (**thousand**
tonnes), and `build_direction_rows`/`build_cargo_rows` were passing that raw value straight
into `PortThroughputRow.gross_weight_tonnes`, a field named as if it held real tonnes but
actually holding thousand-tonnes. A silent 1000x understatement. Fixed at the raw -> domain
boundary in `transform/throughput.py` (`THOUSAND_TONNES_TO_TONNES = 1000`), not in the
loader, because the load layer shouldn't need to know about source units at all. All
affected Phase 2 test assertions were updated to match. `continuity.py` needed no change:
it sums whatever's already in `PortThroughputRow.gross_weight_tonnes`, so it inherited the
fix automatically once the values flowing into it were correct.

This is exactly the kind of thing a schema with an honestly-named column catches. If the
column had been left as generically named as `value`, this would have shipped silently.

## Phase 3 — closing the loop on `revised_estimate`

Phase 2 left `revised_estimate` in the `flag_type` enum but unpopulated, noting it needs a
second pull to compare against. The loader now does exactly that. `port_throughput`'s
MERGE statement (`load/upsert.py`, `build_throughput_upsert`) uses `OUTPUT $action,
inserted.throughput_id, deleted.gross_weight_tonnes, inserted.gross_weight_tonnes` so every
upsert reports whether it matched an existing row and, if so, the old and new value. When a
matched row's value changed by more than a floating-point rounding tolerance (0.01 tonnes),
`load/loader.py` emits a `revised_estimate` flag naming the port, cargo type, direction,
year, old value, and new value. Verified with unit tests against a fake cursor
(`tests/unit/test_loader.py`) for both the "changed" and "unchanged" cases. A real
Eurostat revision hasn't happened between any two pulls yet, so this hasn't fired for real
data. It will the first time Eurostat republishes a historical figure.

## Phase 3 — idempotency and load, verified against the real database

Ran the full `port-analytics` CLI (ingest -> transform -> load) twice in a row against the
real free-tier Azure SQL database. Row counts after both runs, queried directly:

| Table | Rows |
|---|---|
| `ports` | 6 |
| `cargo_types` | 7 |
| `port_throughput` | 1,013 |
| `data_quality_flags` | 4 |

Identical after run 1 and run 2, so no duplication. `0 revisions detected` both times, as
expected: the underlying Eurostat data didn't change between the two runs, seconds apart.
The self-referencing `merged_into_port_id` link resolved correctly, with Antwerpen and
Zeebrugge's rows both pointing at Antwerp-Bruges's `port_id`.

## Phase 3 — Azure resource decisions

- **Cost: $0/month.** The Azure SQL Database free offer was expanded (per Microsoft's own
  documentation, updated 2026-08-18) from one free database per subscription to **up to 10
  free General Purpose serverless databases per subscription**, each with its own 100,000
  vCore-second / 32 GB monthly allowance. This project's database is the 2nd of 10 used on
  the subscription (the 1st is PortYard's). Created with
  `--free-limit-exhaustion-behavior AutoPause`, so exceeding the free allowance pauses the
  database until next month rather than billing. Verified after creation via `az sql db
  show`, which reported `useFreeLimit: true` and `freeLimitExhaustionBehavior: "AutoPause"`.
  (The locally-installed Azure CLI's `--help` text claimed the free limit was "allowed on
  one database in a subscription". That's stale relative to the current offer, so this was
  verified empirically on the actual created resource rather than trusting either source
  blindly.)
- **New resource group and logical server**, not `rg-portyard`/`portyard-sql-server`.
  `rg-northern-range-analytics` in `australiaeast` (the free offer locks every free
  database in a subscription to one shared region, and PortYard's is already
  `australiaeast`). A logical SQL server carries no cost of its own, since only databases
  are billed, so a dedicated server costs nothing extra and keeps this project's resources
  cleanly separable from PortYard's for cost tracking, RBAC, and teardown.
