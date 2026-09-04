# Function analysis: src/port_analytics/ingest/eurostat_client.py

This file has one function with real control flow, `fetch_dataset`. It also defines two
supporting types that are not functions (`EurostatAPIError`, an exception class with no
methods; `JsonStatDataset`, a pydantic model with no custom validators) and a structural
`Protocol` (`HttpGetter`) with no body to analyze. Both non-function types are load-bearing
for `fetch_dataset`'s trust boundary, so they are covered as part of its cross-function
dependencies rather than as separate sections.

---

## `fetch_dataset` in src/port_analytics/ingest/eurostat_client.py (L51-L91)

**Purpose:** Sole entry point in the codebase that speaks HTTP/JSON to the Eurostat REST
API. Converts a dataset code and a list of port (`rep_mar`) codes into a validated,
untouched JSON-stat payload, or raises `EurostatAPIError` on any failure. Everything
downstream (`land_raw_response`, and later cleaning stages not in this file) depends on
this function being the only place that decides "this response is usable data" — without
it, malformed or empty Eurostat responses would be landed as if they were real data.

**Inputs & Assumptions:**
- `dataset_code` (str): Eurostat dataset identifier, interpolated directly into the request
  URL path at L64. Trust: in the only current caller (`pull_and_land`, `src/port_analytics/ingest/pull.py:27`)
  it comes from `DATASET_CODES.values()` (`src/port_analytics/config.py:9-12`), a fixed
  internal constant — trusted in practice. As a function parameter with no internal
  validation, a future caller passing an untrusted `dataset_code` would have it placed
  into the URL with no character filtering; nothing in this function constrains it (no
  check exists between L64 construction and the outbound call at L68).
- `rep_mar_codes` (list[str]): Eurostat port codes, spread into the `rep_mar` query
  parameter (L65). Trust: current caller passes `PORT_CODES` (`config.py:19-26`), a fixed
  internal list — trusted in practice. No validation of contents or length in this
  function.
- `session` (`HttpGetter`): injectable HTTP client, `requests.Session()` in production
  (`pull.py:24`). Trust: structurally typed via `Protocol` (L47-48) — this is a
  compile-time/type-checker contract only; nothing at runtime verifies `session` actually
  returns a `requests.Response`-shaped object with `.status_code` and `.json()`. The
  function's correctness (L72, L76) depends on the object returned by `session.get`
  behaving like `requests.Response`.
- `timeout` (float, default 30.0): passed through to `session.get` (L68). Whether it is
  honored depends entirely on the injected session's implementation; nothing in this
  function enforces that the call actually returns within `timeout`.
- Implicit: `EUROSTAT_BASE_URL` (`config.py:5`) — fixed, trusted, not attacker-influenced.
- Precondition (implicit in the docstring, L59-62): caller expects this function to never
  raise anything except `EurostatAPIError` for "any HTTP failure, malformed payload, or a
  dataset with zero data points." This precondition is not fully established — see
  Block-by-Block on L67-70 and L75-78.

**Outputs & Effects:**
- Returns the **untouched parsed JSON body** (`payload`, L91), not a re-serialization of
  the validated pydantic model — the docstring at L26-30 makes this an explicit design
  choice (avoids int→float coercion and dropped unmodelled fields from a pydantic
  round-trip).
- No state mutation, no filesystem or persistent writes in this function.
- One outbound network call (L68) to `f"{EUROSTAT_BASE_URL}/{dataset_code}"`.
- Raises `EurostatAPIError` on: transport failure (L69-70), non-200 status (L72-73),
  invalid JSON (L77-78), schema mismatch (L82-86), or empty `value` map (L88-89).
- Postcondition on successful return: `payload` parses as JSON, its top-level shape
  matches `JsonStatDataset` (see Cross-Function Dependencies), and `payload["value"]` is a
  non-empty mapping. Nothing about internal consistency between `id`, `size`, `dimension`,
  and `value` is a postcondition — see below.

**Block-by-Block:**

```python
# L64-L65
url = f"{EUROSTAT_BASE_URL}/{dataset_code}"
params: dict[str, Any] = {"format": "JSON", "lang": "EN", "rep_mar": rep_mar_codes}
```
- **What:** Builds the request URL and query parameters.
- **Why here:** Precedes the network call; fixes the exact request shape sent.
- **Assumes:** `EUROSTAT_BASE_URL` is a trusted host (true, from `config.py`); `dataset_code`
  contains no characters that would change the URL's meaning (e.g. `/`, `?`, `#`) — nothing
  enforces this within the function.
- **Establishes:** The request target, used at L68.
- **Depended on by:** L68 (the actual call), and transitively by every error message that
  echoes `dataset_code` (L70, L73, L78, L85, L89) — those messages will reflect whatever
  string was passed, unsanitized.

```python
# L67-L70
try:
    response = session.get(url, params=params, timeout=timeout)
except requests.RequestException as exc:
    raise EurostatAPIError(f"request to Eurostat failed for {dataset_code}: {exc}") from exc
```
- **What:** Performs the HTTP GET, converting transport-level failures into
  `EurostatAPIError`.
- **Why here:** First point of contact with the adversarial remote service; isolates
  network failures from later parsing failures.
- **Assumes:** Any failure from `session.get` surfaces as `requests.RequestException` (or a
  subclass). This holds for a real `requests.Session` (connection errors, timeouts, DNS
  failures, too-many-redirects, HTTP errors if `raise_for_status` were used — it isn't
  here — all subclass `RequestException`). It does **not** hold for arbitrary exceptions a
  malformed parameter or a non-standard injected `session` could raise (e.g. a `TypeError`
  from `requests`' own URL-encoding of `params` if `rep_mar_codes` contained non-string
  items, or an `AttributeError` from a fake session missing `.get`) — those are not
  `requests.RequestException` and would propagate out of `fetch_dataset` unwrapped,
  contradicting the docstring's "Raises EurostatAPIError on any HTTP failure" claim (L61).
- **Establishes:** `response` is bound only on the success path; on the exception path the
  function returns via the raised `EurostatAPIError` before reaching L72, so `response` is
  never read in an inconsistent state.
- **Depended on by:** L72 (`response.status_code`) and L76 (`response.json()`) assume
  `response` is a `requests.Response`-like object as declared by `HttpGetter` — not
  runtime-checked (see `session` in Inputs & Assumptions).

```python
# L72-L73
if response.status_code != 200:
    raise EurostatAPIError(f"Eurostat returned HTTP {response.status_code} for {dataset_code}")
```
- **What:** Rejects any non-200 response before attempting to parse it as JSON.
- **Why here:** Placed before `.json()` (L76) specifically to avoid conflating "server
  returned an error page" with "server returned malformed JSON" — an error body (e.g. HTML
  or a Eurostat error JSON) is never handed to the JSON-stat validator.
- **Assumes:** `response.status_code` is a reliable indicator of success; other 2xx codes
  (201, 202, 204, etc.) are treated as failures by this strict equality check — a
  deliberate narrowing, not a bug in this analysis's terms, but worth noting the function
  only accepts exactly `200`.
- **Establishes:** From this point on, the response is treated as a "successful" response
  worth parsing.
- **Depended on by:** L76 onward.

```python
# L75-L78
try:
    payload: dict[str, Any] = response.json()
except ValueError as exc:
    raise EurostatAPIError(f"Eurostat response for {dataset_code} was not valid JSON") from exc
```
- **What:** Parses the response body as JSON.
- **Why here:** After the status check, before schema validation — separates "not JSON at
  all" failures from "JSON but wrong shape" failures (L80-86).
- **Assumes:** `response.json()` raises only `ValueError` (or a subclass) on decode
  failure. This holds for `requests`' own JSON decode errors (`requests.exceptions.JSONDecodeError`
  subclasses `ValueError` in the `requests`/`simplejson` versions in common use), but a
  malformed/adversarial `session` whose `.json()` raises something else (e.g. `TypeError`,
  `RecursionError` from deeply nested JSON, `MemoryError` from a huge body) would propagate
  unwrapped past this function. The type annotation `payload: dict[str, Any]` (L76) is
  **not enforced at runtime** — if `response.json()` returns a JSON array, string, number,
  `null`, or boolean, `payload` is silently bound to a non-dict value at this line; nothing
  here checks `isinstance(payload, dict)`.
- **Establishes:** `payload` holds parsed JSON (of unknown top-level type, despite the
  annotation) on the success path.
- **Depended on by:** L81 (`JsonStatDataset.model_validate(payload)`), which is the first
  point that actually constrains `payload`'s shape — see next block — and L91 (the raw
  return value).

```python
# L80-L86
try:
    validated = JsonStatDataset.model_validate(payload)
except ValidationError as exc:
    raise EurostatAPIError(
        f"Eurostat response for {dataset_code} did not match the expected JSON-stat shape: {exc}"
    ) from exc
```
- **What:** Structurally validates `payload` against `JsonStatDataset` (see Cross-Function
  Dependencies for what that model does and does not check).
- **Why here:** Last gate before the "zero data points" check; rejects payloads that are
  JSON but not JSON-stat-shaped, e.g. an unrelated error object with `{"error": "..."}`
  that nonetheless parsed as valid JSON at L76.
- **Assumes:** `JsonStatDataset.model_validate` raises only `pydantic.ValidationError` on
  any shape mismatch, including non-dict `payload`. This holds for pydantic v2's
  documented behavior on `model_validate`.
- **Establishes:** On success, `validated` satisfies the `JsonStatDataset` field types
  (all fields present with the declared shallow types — see model analysis below). This is
  the function's only structural guarantee about `payload`'s top-level shape.
- **Depended on by:** L88 (`validated.value`) and, transitively, by every consumer of the
  returned `payload` downstream (`land_raw_response`, `src/port_analytics/ingest/landing.py:12-27`,
  and any later cleaning stage) — those consumers can assume the nine top-level
  `JsonStatDataset` fields exist with the declared shallow types, but **cannot** assume
  anything about the internal consistency of `id`, `size`, `dimension`, and `value`
  (see Open Questions).

```python
# L88-L89
if not validated.value:
    raise EurostatAPIError(f"Eurostat returned zero data points for {dataset_code}")
```
- **What:** Rejects a structurally valid but empty dataset.
- **Why here:** After schema validation, since `validated.value` only exists once L81
  succeeds.
- **Assumes:** An empty `value` dict means "no data" in every case that matters to callers;
  this is a business-rule assumption about Eurostat's response semantics, not something
  established elsewhere in this file.
- **Establishes:** The invariant callers actually rely on per the docstring (L61-62): a
  returned `payload` always has at least one entry in `value`.
- **Depended on by:** Every downstream consumer of the returned payload that assumes "if
  `fetch_dataset` returned, there is at least one data point" — none of them are in this
  file, but `pull_and_land` (`pull.py:27-28`) immediately lands whatever is returned
  without re-checking.

```python
# L91
return payload
```
- **What:** Returns the original parsed dict, not `validated.model_dump()`.
- **Why here:** Deliberate, per docstring L26-30 — avoids pydantic's round-trip coercions
  (int→float in `value`, dropped unmodelled fields).
- **Assumes:** `payload` is still the same object referenced at L76; nothing between L76 and
  L91 mutates it (confirmed — `JsonStatDataset.model_validate` does not mutate its input).
- **Establishes:** The public contract that the returned dict is a faithful, unmodified
  copy of what Eurostat sent, constrained only by the `JsonStatDataset` shape check having
  passed and `value` being non-empty.
- **Depended on by:** `land_raw_response` (`landing.py:26`), which `json.dumps`s this value
  as-is for the raw audit-trail layer — that function's correctness depends on `payload`
  being JSON-serializable, which holds because it originated from `response.json()` (only
  JSON-native types) and was never touched by non-JSON-safe mutation in this function.

**Cross-Function Dependencies:**
- `JsonStatDataset.model_validate` (internal, `L23-44` / L81): `fetch_dataset` depends on
  this to establish that `payload` is dict-shaped with nine required top-level keys of the
  declared shallow types. What it does **not** establish, and what `fetch_dataset` does
  not check afterward either:
  - No cross-field consistency between `id` (list[str]), `size` (list[int]),
    `dimension` (dict[str, Any], **fully unvalidated content** — any depth, any types), and
    `value` (dict[str, float]) — e.g. nothing checks that `len(id) == len(size)`, that the
    product of `size` corresponds to the index space implied by `value`'s keys, or that
    `dimension`'s keys match `id`. A response could pass validation and the zero-data
    check with `id`/`size`/`dimension` internally self-contradictory relative to `value`.
  - `extension` defaults to `{}` (L44) if absent — no content validation when present.
  - `model_config = ConfigDict(populate_by_name=True)` (L33) means the `class_` field can
    be satisfied by either a `"class"` key (the alias, real JSON-stat's key) or a
    `"class_"` key in the input — the model is more permissive than "must look like real
    JSON-stat data" (docstring L27) suggests, since a payload lacking the real `"class"`
    key but containing `"class_"` would still validate.
  - No `extra="forbid"` is set, so arbitrary additional top-level keys in `payload` are
    silently accepted and ignored by the model (consistent with, and required by, the
    "raw dict landed as-is" design at L28-30) — but this also means the shape check
    provides no signal about unexpected additional top-level content.
  - `value: dict[str, float]` requires `value` to be the JSON-stat *sparse/dict* encoding;
    a JSON-stat response using the *dense array* encoding for `value` (valid per the
    JSON-stat 2.0 spec) would fail validation and be rejected as `EurostatAPIError` at
    L82-86, not silently mishandled — but this is a narrower acceptance surface than "any
    valid JSON-stat 2.0 document."
- `session.get` (external-black-box, production instance is `requests.Session.get`):
  `fetch_dataset` depends on it to (a) actually perform network I/O against
  `EUROSTAT_BASE_URL`, (b) return an object with `.status_code: int` and `.json() -> Any`,
  (c) raise only `requests.RequestException` subclasses on failure, and (d) respect the
  `timeout` argument. (b) and (c) are not runtime-enforced for non-`requests.Session`
  injected sessions (the `HttpGetter` Protocol at L47-48 is structural typing, checked only
  by static type checkers, not at runtime).
- `response.json()` (external-black-box for a non-`requests` session; well-defined for
  `requests.Response`): depended on to return `dict`-shaped data on success and to signal
  all decode failures via `ValueError`. Not enforced to actually return a `dict` (see
  L75-78 block above).
- Callers: `pull_and_land` (`pull.py:19-29`) calls `fetch_dataset` once per dataset code in
  `DATASET_CODES.values()` and immediately passes the returned `payload` to
  `land_raw_response` (`pull.py:28`) with no further validation — it fully trusts
  `fetch_dataset`'s postcondition (valid JSON-stat shape, non-empty `value`). If
  `fetch_dataset` raises `EurostatAPIError`, `pull_and_land` does not catch it (no
  try/except in `pull.py`), so the whole pull run aborts on the first dataset that fails —
  partial landing of prior datasets in `landed` is lost from the return value, though any
  files already written by `land_raw_response` in earlier loop iterations remain on disk.
- Shared state: none within this file; `fetch_dataset` is stateless beyond the `session`
  object it's given, which it does not mutate.
- Invariant couplings: the "raw, as-received" landing invariant that `land_raw_response`'s
  docstring claims (`landing.py:1-2`, "writes Eurostat responses to disk exactly as
  received") is only as strong as `fetch_dataset`'s L91 guarantee that `payload` is
  untouched — which holds structurally (confirmed above) but is coupled to the shallow,
  non-cross-validated `JsonStatDataset` shape check, so "exactly as received" and
  "internally self-consistent JSON-stat data" are not the same guarantee.

**Open Questions:**
- Unclear whether `requests.Response.json()` in the pinned `requests` version raises
  exclusively `ValueError`-derived exceptions on all malformed-JSON inputs (including
  pathological/deeply-nested bodies that might hit Python's recursion limit) — need to
  check the installed `requests`/`simplejson` version's decode-error hierarchy.
- Unclear whether pydantic v2's lax-mode coercion for `value: dict[str, float]` accepts
  boolean values (Python `bool` is a subtype of `int`) as valid floats, which would let a
  response substitute `true`/`false` for numeric measurements without failing validation —
  need to check pydantic's float validator behavior for `bool` input under the default
  (non-strict) mode used here.
- No size limit is applied to the response body before `response.json()` parses it
  (L76) or before `JsonStatDataset.model_validate` walks it (L81) — unclear whether the
  production `requests.Session` config (not shown in this file) imposes any response-size
  cap; as written here, an arbitrarily large adversarial response is fully buffered and
  parsed.
- Unclear whether any caller other than `pull_and_land` invokes `fetch_dataset` with a
  `dataset_code` or `rep_mar_codes` value that does not originate from the trusted
  `config.py` constants — no other call sites were found in `src/port_analytics/`, but the
  function's public signature does not itself restrict input origin.
