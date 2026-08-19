# Data quality notes — Phase 1 investigation

Working notes from the live-API investigation on 2026-08-19, before any ingestion code was
written. This is the durable record of what was found; `data_quality_flags` rows populated
in Phase 2 should trace back to the findings here rather than rediscovering them.

## Dataset codes

The build spec originally listed `mar_mg_am_pwhd` and `mar_go_am`. Both 404 against the live
API — they don't exist. Verified directly against
`https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{code}?format=JSON&lang=EN`,
not from search snippets alone (an initial search-engine summary suggested other plausible-looking
but also-wrong codes, e.g. `mar_mg_am_cwhc`, `mar_mg_aa_cwh` — the latter is real but is a
country-level aggregate, not per-port, so it's the wrong granularity for this project).

The real codes, confirmed by fetching each and inspecting `dimension.rep_mar.category`:

| Purpose | Code | Title | Dimensions |
|---|---|---|---|
| Goods by direction | `mar_mg_aa_pwhd` | Gross weight of goods handled in the top 20 EU ports by direction | `freq, unit, direct, rep_mar, time` |
| Goods by cargo type | `mar_mg_am_pwhc` | Gross weight of goods handled in the top 20 EU main ports by type of cargo | `freq, unit, cargo, rep_mar, time` |

Both cover **20 years, 2005–2024**, for our 6 `rep_mar` codes (see below) — well past the
spec's 10-year target. `docs/data-project-build-spec.md` §2 has been corrected in place.

Note the inconsistent infix: `mar_mg_aa_pwhd` (`aa`) vs `mar_mg_am_pwhc` (`am`). Not a typo
on our part — that's genuinely how Eurostat named these two related datasets.

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

Confirmed directly (not inferred) by checking per-year presence of `BE_0BE003` /
`BE_0BEANR` / `BE_0BEZEE` in the `value` map:

- `BE_0BEANR` and `BE_0BEZEE`: present every year 2005–2021, absent every year 2022–2024.
- `BE_0BE003`: absent every year 2005–2021, present every year 2022–2024.

The cutover is **clean** — no overlap year where both the legacy and merged codes report
(good: no double-counting risk), but also no continuous "Antwerp-Bruges" series exists as a
single Eurostat identifier. Pre-2022 Antwerp-Bruges totals must be computed by summing
`BE_0BEANR + BE_0BEZEE`. This is exactly the `port_merger` flag scenario in the schema —
Phase 2 needs a transform step that builds the combined pre-2022 series and flags each
synthesized row with `port_merger`, pointing at `merged_into_port_id` on the `ports` table.

Sanity check on magnitude: Antwerpen 2021 (215,852) + Zeebrugge 2021 (40,130) = 255,982
thousand tonnes, vs. Antwerp-Bruges 2022 (254,257) — consistent scale, supports treating
these as the same underlying port complex across the cutover.

## Finding 2 — phantom `UNK` cargo category (`outlier_suspected` or a new flag type)

The cargo dataset's `cargo` dimension includes a category `UNK` ("Unknown"), but it has
**zero data points across every port and every year** — confirmed via the API's own
`extension["positions-with-no-data"]` field, which lists `cargo` position 7 (`UNK`) as
entirely empty, not just missing for our 6 ports. This is a whole-category gap baked into
the dataset itself, not a per-row transform issue. Worth a documentation note rather than a
per-row flag; may not need a `data_quality_flags` row at all since there's no row to attach
it to — decide in Phase 2 whether this warrants extending the flag_type list.

## Finding 3 — Hamburg reports zero `RO_MNSP` (non-self-propelled ro-ro) rows

Every other port in the set has at least some `RO_MNSP` (Ro-Ro, mobile non-self-propelled
units) data; Hamburg has none, for any of the 20 years. Two explanations are equally
plausible from the API alone: (a) genuinely all of Hamburg's ro-ro traffic is
self-propelled, so the category is correctly empty, or (b) Hamburg doesn't report this
sub-category and it's being suppressed/omitted rather than genuinely zero. **Cannot be
disambiguated from the JSON-stat API response** — see Finding 4. Flag as
`outlier_suspected` in Phase 2 with a description noting this ambiguity rather than
asserting a cause we can't verify.

## Finding 4 — no confidentiality flag is exposed in this API format

Eurostat's JSON-stat 2.0 response has no `status` key (checked: not present at top level,
not present per-dimension, not present per-value). The only flag-like signal is
`extension["positions-with-no-data"]`, which only reports whole-category emptiness (see
Finding 2), not per-cell suppression reasons. This means: **a missing (port, year, cargo)
combination is indistinguishable between "confidential", "not collected", and "not
applicable" using this API.** We can reliably detect *that* a value is missing (absence
from the sparse `value` map — this is how `fetch_dataset`/Phase 2 parsing works), but not
*why*. Getting the actual confidentiality reason would require Eurostat's bulk TSV/SDMX
downloads (which do carry footnote flags like `c` for confidential) or manual
cross-reference against metadata pages — both out of scope per the spec's "no third data
source" / time-box rules.

**Implication for `data_quality_flags`:** the `suppressed_confidential` flag type should be
used cautiously — only when there's independent evidence (e.g. a footnote from the
statistics-explained article, or an obviously-implausible pattern like a large port missing
one specific year sandwiched between two present years). Default to `missing_year` for
plain absence; don't guess `suppressed_confidential` just because a value is missing.

## Finding 5 — units are consistent between the two datasets we use, but not universally

Both target datasets report the tonnage figure in `THS_T` (thousand tonnes):
- `mar_mg_aa_pwhd` units: `THS_T`, `RT_PRE` (growth rate on previous period)
- `mar_mg_am_pwhc` units: `THS_T`, `PC_TOT` (percentage of total)

No mismatch between the two datasets we're actually ingesting. However, the country-level
dataset `mar_mg_aa_cwh` (not used — wrong granularity, see above) additionally has a
`T_HAB` (tonnes per capita) unit, so `unit_mismatch` is a real risk in this dataset family
generally — worth keeping the flag type even though it isn't triggered by our two datasets
today, in case a future pull touches a dataset with mixed units.

## Value encoding, for whoever writes the Phase 2 parser

`value` is a **sparse dict** keyed by a flat, row-major index string: with `id` giving
dimension order and `size` giving each dimension's category count, the last dimension in
`id` (`time`) varies fastest. Strides are computed as
`stride[i] = product(size[i+1:])`, and the flat index for a set of per-dimension category
positions is `sum(position[i] * stride[i] for i in dims)`. A key's absence from `value`
means missing data, not zero — there is no explicit null. Verified this by hand in Python
against real API responses; a first attempt at doing this arithmetic via an LLM web-fetch
summary got the stride order backwards and produced a wrong port/year table, so don't trust
a summarized description of the index math — decode it directly.
