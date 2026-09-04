# Function Analysis: `src/port_analytics/ingest/pull.py` and `src/port_analytics/ingest/landing.py`

---

## `pull_and_land` in src/port_analytics/ingest/pull.py (L19-L29)

**Purpose:** Phase-1 ingest driver — pulls both configured Eurostat datasets for the fixed set of Northern
Range ports and writes each raw response to disk via `land_raw_response`. It is the only place that ties a
dataset code, its fetched payload, and a landing timestamp together, and the only caller of
`land_raw_response` in the codebase (confirmed by `land_raw_response` grep below). Everything downstream
(`cli.py` L26-L44) reads back the files this function writes, keyed by filename prefix.

**Inputs & Assumptions:**
- `landing_dir` (Path, default `DEFAULT_LANDING_DIR` = `Path("data/raw")`, `config.py:L28`): destination
  directory. Trust: **semi-trusted** — reaches here from `cli.py:L23` as a Typer CLI option (`landing_dir: Path
  = DEFAULT_LANDING_DIR`), i.e. operator-supplied at invocation time. Nothing in `pull.py`, `cli.py`, or
  `landing.py` restricts it to a subtree of the project or rejects absolute/parent-traversing paths.
- `session` (`HttpGetter | None`): optional injected HTTP client (`eurostat_client.py:L47-L48` Protocol). Trust:
  trusted when omitted (falls back to `requests.Session()` at L24); trusted-by-construction when supplied,
  since only test code and this module's own default construct it. No `isinstance`/structural check is
  performed — an object missing a compatible `.get()` fails inside `fetch_dataset`, not here.
- Implicit: `DATASET_CODES` (`config.py:L9-L12`, two hardcoded strings) and `PORT_CODES`
  (`config.py:L19-L26`, six hardcoded strings) — both fixed, trusted, no external input reaches them.
- Implicit: wall clock via `datetime.now(UTC)` (L28), read fresh on every loop iteration.
- Precondition: none stated on `landing_dir` being writable or safe; establishment is left entirely to
  `land_raw_response`'s `mkdir(parents=True, exist_ok=True)` (`landing.py:L23`).

**Outputs & Effects:**
- Returns `list[Path]` of landed file paths, one per dataset code, in `DATASET_CODES` iteration order
  (`goods_by_direction` then `goods_by_cargo_type`, per dict insertion order at `config.py:L9-L12`).
- Side effect: writes one file per dataset to `landing_dir` via `land_raw_response` (L28) — see that
  function's effects.
- Side effect: performs one outbound HTTP GET per dataset via `fetch_dataset` → `session.get` (L27,
  `eurostat_client.py:L68`).
- No exception handling around either the fetch or the land call — `EurostatAPIError` from `fetch_dataset`
  or any exception from `land_raw_response` (e.g. `OSError` from `mkdir`/`write_text`) propagates
  unmodified out of `pull_and_land`.

**Block-by-Block:**

```python
# L23-L24
http = session if session is not None else requests.Session()
landed: list[Path] = []
```
- **What:** Resolves the HTTP client to use for the whole run.
- **Why here:** Single resolution point so both loop iterations share one session/connection pool.
- **Assumes:** a bare `requests.Session()` needs no further configuration (auth, proxies, retries) for the
  Eurostat endpoint.
- **Establishes:** `http` is non-None for the rest of the function.
- **Depended on by:** L27.

```python
# L26-L28
for dataset_code in DATASET_CODES.values():
    payload = fetch_dataset(dataset_code, PORT_CODES, http)
    landed.append(land_raw_response(dataset_code, payload, landing_dir, datetime.now(UTC)))
```
- **What:** For each of the two fixed dataset codes, fetches and immediately lands the payload.
- **Why here:** Sequential, one dataset fully round-tripped (network + disk) before the next starts.
- **Assumes:** `fetch_dataset` either returns a JSON-serializable dict or raises — see callee analysis. Also
  assumes each `land_raw_response` call either succeeds or raises; there is no partial-success bookkeeping.
- **Establishes:** nothing that survives a mid-loop exception — if the second call to `fetch_dataset` or
  `land_raw_response` raises, the first dataset's file has already been written to `landing_dir` (a real,
  irreversible side effect) but is not present in any list the caller receives, because the function raises
  before reaching L29. The on-disk state and the function's (non-)return value diverge on this path.
- **Depended on by:** L29 (success path) and every caller that treats a non-raising return as "both datasets
  landed."

```python
# L29
return landed
```
- **What:** Returns the two landed paths on the all-succeeded path only.
- **Why here:** Final statement; unreachable if either loop iteration raised.
- **Assumes:** callers only inspect `landed` after confirming no exception was raised.
- **Establishes:** postcondition "both dataset files exist under `landing_dir`" — but only for the value
  actually returned; nothing establishes this for the exception path.

**Cross-Function Dependencies:**
- **Callee `fetch_dataset` (internal, `eurostat_client.py:L51-L91`):** read in full.
  - Network/HTTP-exception path (L67-L70): wraps `requests.RequestException` in `EurostatAPIError` and
    re-raises. `pull_and_land` does not catch this.
  - Non-200 path (L72-L73): raises `EurostatAPIError` without inspecting body — a 4xx/5xx page is never
    parsed or landed.
  - Invalid-JSON path (L75-L78): raises `EurostatAPIError`.
  - Schema-mismatch path (L80-L86): validates against `JsonStatDataset` (`eurostat_client.py:L23-L44`) and
    raises `EurostatAPIError` on any `ValidationError` — this is the only structural check that the payload
    has the fields `land_raw_response` and downstream transform code expect.
  - Empty-data path (L88-L89): raises if `validated.value` is empty, even though the payload otherwise
    validated — `pull_and_land` depends on this to avoid landing a syntactically valid but empty dataset.
  - Success path (L91): **returns the original parsed `payload` dict, not the validated/coerced pydantic
    model** (explicitly noted at `eurostat_client.py:L28-L30`) — `pull_and_land` depends on this so that
    `land_raw_response` lands the response byte-for-byte-equivalent JSON, not a re-typed copy. `pull_and_land`
    itself does not re-validate `payload` before passing it on; the only gate is inside `fetch_dataset`.
- **Callee `land_raw_response` (internal, `landing.py:L12-L27`):** see full analysis below. `pull_and_land`
  depends on it to (a) create `landing_dir` if missing, (b) produce a filename that embeds `dataset_code` and
  a timestamp so two calls in the same run don't collide, and (c) not silently drop or transform `payload`.
  Point (b) is only actually distinct within one `pull_and_land` call because the two `dataset_code` values
  differ, not because of a timestamp guarantee — see landing analysis.
- **Callers:** `cli.py:L26` (`run()`), which passes an operator-supplied `landing_dir` and no `session`
  (always a fresh `requests.Session()`), then at L30-L37 reads the returned paths back with
  `next(p for p in landed_paths if p.name.startswith(dataset_code))`. That lookup assumes the filename
  produced by `land_raw_response` starts with exactly the `dataset_code` string passed to
  `pull_and_land` — true today because neither `"mar_mg_aa_pwhd"` nor `"mar_mg_am_pwhc"` is a prefix of the
  other (`config.py:L10-L11`), but nothing in `pull_and_land` or `land_raw_response` enforces prefix-uniqueness
  of `DATASET_CODES` values in general. `pull.py:L32-L34` (`__main__` block) calls `pull_and_land()` with all
  defaults and only prints each returned path — no error handling either.
- **Shared state:** the filesystem under `landing_dir`, shared with `land_raw_response` and (read-only) with
  `cli.py`'s later `read_text` calls.
- **Invariant coupling:** the docstring's implicit promise (`pull.py:L1-L4`, "land them raw") that landing is
  atomic per dataset holds per-dataset (each `land_raw_response` call either fully writes or raises before
  returning) but not per-run: a two-dataset pull can leave exactly one file landed with no signal in the
  return value distinguishing that from zero.

**Open Questions:**
- unclear; need to inspect whether any caller retries `pull_and_land` after a partial failure, and if so
  whether landing a duplicate-timestamp file for the dataset that already succeeded is expected or guarded
  against anywhere outside this function.
- unclear; need to inspect `cli.py`'s exception handling (none observed between L23 and L54) to know whether
  a mid-run `EurostatAPIError` here surfaces to the operator as anything more specific than an unhandled
  traceback.

---

## `land_raw_response` in src/port_analytics/ingest/landing.py (L12-L27)

**Purpose:** The sole write path from the ingest layer to disk. Serializes a Eurostat payload as-is and
writes it under a timestamped, dataset-coded filename, intended (per docstring L19-L21) as an append-only raw
audit trail that a re-run never overwrites.

**Inputs & Assumptions:**
- `dataset_code` (str): used verbatim as a filename prefix at L25. Trust: **trusted in practice, unchecked in
  code** — its only caller, `pull_and_land` (`pull.py:L28`), always passes one of the two hardcoded
  `DATASET_CODES.values()` (`config.py:L9-L12`). Nothing inside `land_raw_response` validates that
  `dataset_code` contains no path separators, no `..` segments, and no characters invalid in a filename on
  the host OS — the function has no defense of its own against a caller passing an unsanitized value into
  what becomes a path component at L25.
- `payload` (`dict[str, Any]`): written verbatim via `json.dumps` (L26). Trust: trusted under the current
  caller, since `fetch_dataset` only returns a payload that already round-tripped through `response.json()`
  and passed `JsonStatDataset` validation (`eurostat_client.py:L81-L89`) — i.e., it is guaranteed
  JSON-serializable and non-empty on every path that reaches `land_raw_response` today. Nothing in
  `land_raw_response` itself re-checks JSON-serializability; a differently-sourced caller passing a dict with
  non-serializable values would raise an uncaught `TypeError` from `json.dumps` (L26) after the directory at
  L23 has already been created.
- `landing_dir` (Path): destination directory. Trust: semi-trusted, inherited unchanged from `pull_and_land`'s
  `landing_dir` — see that function's analysis. `land_raw_response` performs no containment check (e.g., no
  verification that the resolved `out_path` stays under `landing_dir`).
- `pulled_at` (datetime): used only for `strftime("%Y%m%dT%H%M%SZ")` (L24), one-second resolution. Trust:
  trusted — always `datetime.now(UTC)` from the sole caller (`pull.py:L28`), read fresh per call.
- Implicit: filesystem state at `landing_dir` and at the computed `out_path` — not inspected before writing.
- Precondition (docstring, L20-21): "re-running the pull never overwrites a prior landing." Nothing in this
  function establishes that precondition as a general guarantee; it holds only as a corollary of (a)
  `dataset_code` differing across the two datasets in one run, and (b) `pulled_at` differing to one-second
  resolution across separate runs. Two calls with the same `dataset_code` within the same wall-clock second
  produce the same `out_path`.

**Outputs & Effects:**
- Returns the `Path` written to.
- State write: creates `landing_dir` (and all missing parents) if absent, via `mkdir(parents=True,
  exist_ok=True)` (L23) — silently succeeds whether or not the directory already existed; does not
  distinguish "created" from "already there."
- State write: `out_path.write_text(json.dumps(payload), encoding="utf-8")` (L26) — opens in text-write mode,
  which **truncates and overwrites** any pre-existing file at `out_path` (including following a symlink at
  that path, since `Path.write_text` performs a normal `open()`), and creates it otherwise. No exclusive-create
  (`"x"` mode) or existence check precedes the write, so the "never overwrites" claim in the docstring is not
  enforced by this code — only made true incidentally by timestamp granularity.
- No return-value or exception signal distinguishing "created new file" from "overwrote existing file."

**Block-by-Block:**

```python
# L23
landing_dir.mkdir(parents=True, exist_ok=True)
```
- **What:** Ensures the destination directory tree exists.
- **Why here:** Must precede the write at L26; must also precede filename construction only in the sense
  that both use `landing_dir`, but this is not itself a validation of `landing_dir`'s value.
- **Assumes:** `landing_dir` is not, e.g., a path to an existing regular file (that would raise `FileExistsError`
  from `mkdir`, which propagates uncaught) and that the process has permission to create it.
- **Establishes:** the directory exists (or raises trying) for L25-L26.
- **Depended on by:** L26.

```python
# L24-L25
timestamp = pulled_at.strftime("%Y%m%dT%H%M%SZ")
out_path = landing_dir / f"{dataset_code}_{timestamp}.json"
```
- **What:** Builds the target filename from caller-supplied `dataset_code` and `pulled_at`.
- **Why here:** Computed once, used both for the write and the returned value.
- **Assumes:** `dataset_code` is filename-safe and contains no path-navigating characters (`/`, `\`, `..`) —
  unchecked, see Inputs above. `Path.__truediv__` (`/`) does not sanitize its argument; if `dataset_code`
  contained a separator, the resulting `out_path` would resolve outside `landing_dir`.
- **Establishes:** `out_path`, consumed by L26 and the return at L27.
- **Depended on by:** L26, L27, and (via the returned `Path`) `cli.py`'s later `read_text` and
  `startswith(dataset_code)` matching.

```python
# L26
out_path.write_text(json.dumps(payload), encoding="utf-8")
```
- **What:** Serializes `payload` to JSON text and writes it to `out_path`, creating or truncating as needed.
- **Why here:** Final effect of the function; nothing after it can fail without the file already being
  written.
- **Assumes:** `payload` is JSON-serializable (true for every payload reaching this function today, per
  `fetch_dataset`'s validation — see Inputs); assumes no concurrent writer targets the same `out_path`
  (no locking or exclusive-create is used).
- **Establishes:** the file at `out_path` contains the payload's JSON representation. Does **not** establish
  that this was the first write to that path — see Outputs & Effects above.
- **Depended on by:** `cli.py:L31-L35`, which reads this file back by path membership.

**Cross-Function Dependencies:**
- No internal callees beyond stdlib (`Path.mkdir`, `Path.write_text`, `json.dumps`, `datetime.strftime`).
- **Callee `Path.mkdir` (external-source-available, stdlib):** raises `FileExistsError` if `landing_dir`
  exists as a non-directory, `PermissionError`/`OSError` on ACL or filesystem failure — none of these are
  caught here, so they propagate to `pull_and_land` and, from there, unhandled to `cli.py`.
- **Callee `Path.write_text`/`json.dumps` (external-source-available, stdlib):** `json.dumps` raises
  `TypeError` on non-serializable content; `write_text` raises `OSError` variants on disk-full, permission,
  or path-length failures on the target OS. Both propagate uncaught.
- **Callers:** `pull_and_land` (`pull.py:L28`), the only caller in the codebase (confirmed by repo-wide
  search) — supplies trusted `dataset_code`/`payload` today, and an operator-controlled `landing_dir`. Any
  future or test caller (e.g. `tests/unit/test_landing.py:L14,L25,L35-L39`) that passes an untrusted
  `dataset_code` would inherit the unvalidated-filename-component behavior described above; the unit tests
  only ever pass hardcoded literal dataset codes, so they do not exercise that path.
- **Shared state:** the filesystem tree rooted at `landing_dir`, shared with `pull_and_land` (writer, via this
  function) and `cli.py` (reader, via `Path.read_text` at L32-L34 keyed on filename prefix).
- **Invariant coupling:** the "append-only audit trail" invariant claimed in the module docstring
  (`landing.py:L1-L2`) and function docstring (L19-21) is a property of the *combination* of unique
  `dataset_code` values and second-resolution timestamps supplied by the current caller, not a property
  enforced by `land_raw_response` itself. If either assumption breaks (duplicate `dataset_code` in one run,
  or two runs within the same UTC second), the "append-only" guarantee silently becomes "last write wins"
  with no error or signal.

**Open Questions:**
- unclear; need to inspect whether any process ever calls `pull_and_land`/`land_raw_response` more than once
  per second in practice (e.g. retry logic, scheduled overlapping runs) — that determines whether the
  same-second overwrite path is reachable operationally.
- unclear; need to inspect what value of `landing_dir` operators actually pass via the `port-analytics` CLI
  (`cli.py:L23`) in deployment — determines whether the unchecked `landing_dir`/`dataset_code` path
  construction ever sees a value that isn't fully operator/config-controlled.
