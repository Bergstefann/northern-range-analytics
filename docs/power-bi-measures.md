# Power BI: connecting, data model, and the four DAX measures

Verified 2026-08-19 against the live `NorthernRangeAnalytics` Azure SQL database (6 ports,
7 cargo types, 1,013 `port_throughput` rows, 4 `data_quality_flags` rows, years
2005–2024). Power BI Desktop isn't available in the environment these measures were
authored in, so **this is a manual setup guide, not a `.pbip` project file** — a
hand-authored `.pbip`/TMDL project can't be opened and validated without Desktop, and a
broken project file someone can't diagnose is worse than clear instructions. The DAX below
is correct against the real schema and real data by hand-tracing against the live database
(see the double-counting check below); the M query gets the four tables into Desktop in
one paste instead of clicking through the Get Data wizard four times.

## Connect

1. Power BI Desktop → **Get Data** → **Blank Query** → **Advanced Editor**, paste one of
   the four queries below, repeat for each table. (Equivalent to Get Data → Azure SQL
   Database → server `northern-range-sql-server.database.windows.net`, database
   `NorthernRangeAnalytics`, then selecting each table in the navigator — the M queries
   below just skip the wizard.)
2. **Import** mode (not DirectQuery) — the whole dataset is ~1,000 rows, comfortably small
   enough to import, and Import mode means the report doesn't depend on the free-tier
   database staying online (or awake — it auto-pauses when idle) while someone's viewing it.
3. Authentication: **Database** (SQL auth) when prompted. Username/password are in your
   `.env` — never enter them anywhere that gets committed.

```m
// Ports
let
    Source = Sql.Database("northern-range-sql-server.database.windows.net", "NorthernRangeAnalytics"),
    dbo_ports = Source{[Schema="dbo",Item="ports"]}[Data]
in
    dbo_ports

// Cargo types
let
    Source = Sql.Database("northern-range-sql-server.database.windows.net", "NorthernRangeAnalytics"),
    dbo_cargo_types = Source{[Schema="dbo",Item="cargo_types"]}[Data]
in
    dbo_cargo_types

// Port throughput
let
    Source = Sql.Database("northern-range-sql-server.database.windows.net", "NorthernRangeAnalytics"),
    dbo_port_throughput = Source{[Schema="dbo",Item="port_throughput"]}[Data]
in
    dbo_port_throughput

// Data quality flags
let
    Source = Sql.Database("northern-range-sql-server.database.windows.net", "NorthernRangeAnalytics"),
    dbo_data_quality_flags = Source{[Schema="dbo",Item="data_quality_flags"]}[Data]
in
    dbo_data_quality_flags
```

## Relationships

Power BI's auto-detect should find these from the FK columns, but if not, set manually —
all **many-to-one**, single direction, from `port_throughput`/`data_quality_flags` toward
the lookup tables:

| From | To | On |
|---|---|---|
| `port_throughput` | `ports` | `port_id` |
| `port_throughput` | `cargo_types` | `cargo_type_id` |
| `data_quality_flags` | `ports` | `port_id` |
| `data_quality_flags` | `port_throughput` | `throughput_id` |
| `ports` | `ports` | `merged_into_port_id` → `port_id` (self-referencing — Power BI may need this one added manually) |

## The one thing that will silently wreck every measure if missed

`port_throughput`'s grain is **not** one row per (port, year). It's one row per (port,
cargo_type, year, direction) — meaning a single port-year has up to 9 rows: one
`cargo_type_code = 'TOTAL'` row per direction (`total`/`inbound`/`outbound`), plus one row
per real cargo-type breakdown (`LBK`, `DBK`, `LCNT`, `RO_MSP`, `RO_MNSP`, `OTH`) at
`direction = 'total'`. A naive `SUM(port_throughput[gross_weight_tonnes])` with no filter
sums across *all* of those — total, breakdown, and directional rows together — and wildly
overcounts. Every measure below that wants "total tonnage for a port-year" filters to
`cargo_type_code = 'TOTAL'` and `direction = 'total'` specifically.

**Second gotcha, and the more dangerous one:** because of the Antwerp continuity decision
(see `docs/data-quality-notes.md`), `ports` has 6 rows but only 4 are independent —
Antwerpen and Zeebrugge's tonnage for 2005–2021 is *also* included in Antwerp-Bruges's
derived rows for those same years. Verified directly against the live data: 2021 total
tonnage is Antwerpen 215,852,000 + Zeebrugge 40,130,000 = 255,982,000, and Antwerp-Bruges's
own 2021 row (`source = 'derived_sum:BE_0BEANR+BE_0BEZEE'`) is exactly 255,982,000. Summing
across all 6 `ports` rows for any pre-2022 year triple-counts Belgian tonnage. Every measure
that aggregates "across all ports" filters to `ports[merged_into_port_id] = BLANK()` —
the 4 non-merged ports (Antwerp-Bruges, Hamburg, Rotterdam, Gdansk), each of which already
carries a correct, continuous, non-duplicated series across every year.

## Base measure (supporting infrastructure, not one of the 4)

```dax
Total Tonnage (TY) =
CALCULATE(
    SUM(port_throughput[gross_weight_tonnes]),
    cargo_types[cargo_type_code] = "TOTAL",
    port_throughput[direction] = "total"
)
```

Every measure below builds on this — it isolates the "grand total" grain described above.
`year` is a plain `SMALLINT` column, not a Date table (annual-only data doesn't need
month/quarter granularity), so year-over-year comparisons below use explicit integer year
arithmetic instead of `DATEADD`/`SAMEPERIODLASTYEAR`.

## 1. YoY tonnage growth % by port

```dax
Total Tonnage (PY) =
CALCULATE(
    [Total Tonnage (TY)],
    FILTER(
        ALL(port_throughput[year]),
        port_throughput[year] = SELECTEDVALUE(port_throughput[year]) - 1
    )
)

YoY Tonnage Growth % =
DIVIDE([Total Tonnage (TY)] - [Total Tonnage (PY)], [Total Tonnage (PY)])
```

Put `ports[port_name]` and `port_throughput[year]` on rows/axis, this measure as the value.
Format as percentage.

## 2. 3-year rolling average throughput

```dax
3-Year Rolling Avg Tonnage =
VAR CurrentYear = SELECTEDVALUE(port_throughput[year])
VAR WindowYears =
    FILTER(
        ALL(port_throughput[year]),
        port_throughput[year] <= CurrentYear && port_throughput[year] > CurrentYear - 3
    )
RETURN
    IF(
        COUNTROWS(WindowYears) = 3,
        AVERAGEX(
            WindowYears,
            CALCULATE(
                SUM(port_throughput[gross_weight_tonnes]),
                cargo_types[cargo_type_code] = "TOTAL",
                port_throughput[direction] = "total"
            )
        )
    )
```

Blank for the first two years of any port's series (no full 3-year window yet) rather than
averaging a partial window — a 2-year "3-year average" would be misleading, not just
approximate.

## 3. Rank by cargo type per year

```dax
Cargo Type Rank =
VAR RankingContext = FILTER(ALLSELECTED(cargo_types), cargo_types[cargo_type_code] <> "TOTAL")
RETURN
    RANKX(
        RankingContext,
        CALCULATE(
            SUM(port_throughput[gross_weight_tonnes]),
            port_throughput[direction] = "total"
        ),
        ,
        DESC,
        DENSE
    )
```

Excludes the `TOTAL` pseudo-cargo-type from the ranking pool — ranking it alongside the
real breakdown types would be meaningless (it's always the largest by construction, being
the sum of the others). Use in a table with `cargo_types[cargo_type_name]` on rows, a port
and year selected via slicers — ranks which cargo type (containers, dry bulk, liquid bulk,
either Ro-Ro type, other) dominates that port's mix that year.

## 4. Antwerp's share of total Northern Range volume — the headline measure

```dax
Northern Range Total Tonnage =
CALCULATE(
    [Total Tonnage (TY)],
    ALLSELECTED(ports),
    ports[merged_into_port_id] = BLANK()
)

Antwerp Share of Northern Range % =
VAR AntwerpTonnage =
    CALCULATE(
        [Total Tonnage (TY)],
        ports[eurostat_code] = "BE_0BE003"
    )
RETURN
    DIVIDE(AntwerpTonnage, [Northern Range Total Tonnage])
```

This measure is the direct payoff of the Phase 2 Antwerp continuity decision: because
Antwerp-Bruges's `port_throughput` rows are continuous across 2005–2024 (real from 2022,
derived before it), this works as one trend line with no special-casing for the
pre-/post-merger boundary in the DAX itself — the data layer already resolved it. Plot
`port_throughput[year]` against this measure as a line chart for the report's headline
visual.
