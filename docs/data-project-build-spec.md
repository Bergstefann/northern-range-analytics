# Build Spec: Northern Range Port Analytics

A data/analytics pipeline over real EU maritime statistics. This is the third and final
project in a three-project portfolio for junior IT roles in Belgium (Antwerp), targeting
Microsoft-stack consultancies.

**Read this whole document before writing any code.** Plan first, then build. Ask before
deviating on anything marked as a decision.

---

## 0. Standing rules for this entire project

- **No `Co-Authored-By` trailers on any commit.** Not on the first commit, not on any commit.
- Conventional-commit messages throughout (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`).
  Small, logical commits as you go — not one giant dump at the end.
- No secrets in git, ever. Connection strings and any credentials go in environment variables
  or user-secrets equivalents. `.gitignore` configured before the first commit lands.
- Tests are not optional and not a coverage-number exercise. They exist to prove the transform
  logic is correct.
- If something in this spec turns out to be wrong when you hit reality (an API endpoint has
  changed, a dataset code doesn't exist, a table has different columns than assumed) — **stop
  and report it rather than silently working around it.** The spec is a plan, not a
  guarantee about the outside world.
- Time box: this is scoped at 3-4 weeks of evenings. If scope starts growing, flag it.

---

## 1. What this project is and why

**The question it answers:** How does the Port of Antwerp-Bruges compare to its Northern Range
competitors on throughput, and what's driving the trend?

The "Northern Range" (Le Havre–Hamburg range) is the real competitive cluster of Northern
European ports. Antwerp, Rotterdam and Hamburg are genuine rivals with a well-documented
rivalry. This is a real logistics question with a real answer in public data, not a
contrived exercise.

**Portfolio position:**

| Pillar | Project | Proves |
|---|---|---|
| Backend | PortYard (.NET, Azure) | Can design and deploy a real system |
| Automation | Invoicer (Python) | Can automate a recurring process, and handle it going wrong |
| **Data** | **This** | Can turn real, messy, external data into something decision-useful |

**Audience:** Belgian IT consultancies (delaware, Cegeka, Cronos member companies, Sopra
Steria, ACA, Axxes) that staff juniors onto Microsoft-stack client work. Azure SQL and
Power BI are deliberate choices for that reason.

---

## 2. Data sources

**Primary: Eurostat maritime transport statistics.** Free, no API key, real REST API.

Target datasets (verify these codes exist and match the described content before building
against them — report if any are wrong or renamed):
- `mar_mg_am_pwhd` — gross weight of goods handled, annual, by port
- `mar_go_am` — goods handled by port, by direction and cargo category (this is the source
  of the container / dry bulk / liquid bulk / ro-ro split)

API base: `https://ec.europa.eu/eurostat/api/dissemination` — investigate the current
statistics API format (JSON-stat is the usual response format) as your first task.

**Ports to pull (five, fixed):**

| Port | Country | Why it's in the set |
|---|---|---|
| Antwerp-Bruges | BE | The subject |
| Rotterdam | NL | Largest Northern Range rival |
| Hamburg | DE | Third major Northern Range player |
| Zeebrugge | BE | Belgian, but ro-ro heavy — different profile to Antwerp |
| Gdansk | PL | Fast-growing outlier for contrast |

Note: Antwerp and Zeebrugge merged into "Port of Antwerp-Bruges" in 2022. Eurostat's
historical data may still report them separately for earlier years. **This is a real data
quality problem and exactly the kind of thing to document rather than paper over.**

**Explicitly NOT doing:** live AIS vessel tracking. Paid, rate-limited, or needs a receiver.
Not worth the time cost. Do not add it.

**Do not add a third data source.** Two is enough to demonstrate integration; a third eats
the time box.

---

## 3. Architecture

```
Eurostat REST API
      │  (Python, requests)
      ▼
  raw landing layer          ← raw responses saved as-is, before any cleaning
      │  (Python, transform)
      ▼
  Azure SQL (relational)     ← cleaned, structured, idempotent loads
      │
      ▼
  Power BI report            ← DAX measures on top
```

Mirror the structural discipline from the other two repos: a clear separation between the
layer that talks to the outside world and the layer that holds business logic, so the
transform logic is testable without network access. Invoicer's ports-and-adapters pattern
is the reference — do the equivalent here, appropriately scaled (this is simpler than
Invoicer; don't over-engineer it into three abstraction layers it doesn't need).

---

## 4. Database schema (Azure SQL)

Genuinely relational. Not one wide flat table.

```
ports
  port_id            PK
  port_name
  country_code
  un_locode
  eurostat_code                    -- whatever Eurostat's own identifier is
  merged_into_port_id  FK nullable -- handles the Antwerp/Zeebrugge merger

cargo_types
  cargo_type_id      PK
  cargo_type_name                  -- containers, dry bulk, liquid bulk, ro-ro, other

port_throughput
  throughput_id      PK
  port_id            FK
  cargo_type_id      FK
  year
  direction                        -- inbound / outbound / total
  gross_weight_tonnes
  source                           -- which Eurostat dataset it came from
  ingested_at

data_quality_flags
  flag_id            PK
  throughput_id      FK nullable   -- nullable: some flags are about missing rows
  port_id            FK nullable
  flag_type                        -- see below
  description
  resolution                       -- what you did about it
  created_at
```

`flag_type` values to use (extend if reality demands, but document why):
`missing_year`, `unit_mismatch`, `revised_estimate`, `port_merger`, `code_change`,
`suppressed_confidential`, `outlier_suspected`

**The `data_quality_flags` table is not optional and not decoration.** It's the cheapest,
highest-signal part of this project. Every non-trivial data issue found during transform
gets a row. This is what turns "I cleaned the data" into something queryable and provable.

---

## 5. Build phases

### Phase 1 — Ingestion and landing

- Investigate the Eurostat API properly first. Confirm the endpoint format, the response
  shape (JSON-stat?), how to filter by port and year, and what the dataset codes actually
  return. **Report what you find before building against it.**
- Python module that pulls the target datasets for the five ports across as many years as
  are available (aim for at least 10 years of history if it exists).
- Land raw responses to disk as-is, before any cleaning touches them. Timestamped.
- Tests: pull succeeds, raw shape matches expectation, a bad/empty response fails loudly
  rather than silently returning nothing.
- Commit as you go.

**Report back at the end of Phase 1 with:** what the API actually returns, how many years
and ports you got, and every data oddity you noticed. Do not proceed to Phase 2 without
this checkpoint.

### Phase 2 — Transform and document the mess

- Transform raw → the schema above.
- Handle the real inconsistencies. Expect: the Antwerp/Zeebrugge merger, missing years for
  some port/cargo combinations, suppressed values (Eurostat marks confidential figures),
  unit inconsistencies, and revised historical figures.
- Populate `data_quality_flags` for everything non-trivial.
- Unit tests on transform logic — same standard as the other two repos. No network access
  required to run them.
- **Write the data-quality findings up as you go**, not retroactively. This becomes the
  README's strongest section (it's the equivalent of Invoicer's postmortem in spirit).

### Phase 3 — Load and orchestration

- Load into Azure SQL.
- **Idempotent.** Re-running the pipeline must not duplicate rows. Carry over the same
  discipline Invoicer already demonstrates.
- Single clear entrypoint (CLI) that runs ingest → transform → load.
- Optional if time allows, not required: a scheduled run (GitHub Actions is fine and
  cheaper than an Azure Function here — Invoicer's README already explains that tradeoff).

**Note on Azure:** an Azure subscription already exists (free tier, resource group
`rg-portyard` in `australiaeast`). Either add a database to the existing SQL server or
create a separate one — **ask before creating anything with a cost implication, and state
expected cost before doing so.** Free tier only.

### Phase 4 — Power BI and README

Power BI report with real DAX measures, not dragged-in fields:
- YoY tonnage growth % by port
- 3-year rolling average throughput
- Rank by cargo type per year
- **Antwerp's share of total Northern Range volume** — this is the headline visual

README to the same standard as the other two repos:
- Accurate claims (test counts, row counts — verify before writing them down)
- Architecture diagram and data model diagram (Mermaid)
- A **Data quality** section documenting what was found and how it was handled
- A design-decisions section covering the real tradeoffs made
- Screenshots of the Power BI report (essential — nobody will open a .pbix file)
- Working links, no placeholders, no `USERNAME` in badge URLs

---

## 6. Definition of done

- [ ] Pipeline runs end to end, idempotently, against live Eurostat data
- [ ] Azure SQL schema genuinely relational
- [ ] At least 3-4 real data quality issues found, flagged in the table, and documented
- [ ] Power BI report with the four DAX measures above, screenshotted in the README
- [ ] Tests on transform logic, passing, runnable without network
- [ ] README matching PortYard and Invoicer's bar
- [ ] Repo has a description and topics set on GitHub
- [ ] Zero `Co-Authored-By` trailers in history

**The sentence this project must let me say truthfully in an interview:**

> "I built a pipeline against real EU government statistics, found and documented real data
> quality issues including a port merger that broke the time series, and the Power BI report
> answers how Antwerp's throughput compares to its Northern Range competitors."

Everything in this spec serves producing that sentence honestly.

---

## 7. What not to do

- No live AIS ingestion
- No third data source
- Don't skip `data_quality_flags` to save time
- Don't let Power BI polish eat Phase 2's time — the schema and data-quality work is what a
  technical interviewer will probe
- Don't over-abstract. This is simpler than Invoicer; it doesn't need Invoicer's full
  provider-protocol structure, just the testability that structure bought.
- Don't write README claims you haven't verified against the actual code and data

---

## Start here

Begin with Phase 1's API investigation. Report what Eurostat actually returns before
building anything against it.
