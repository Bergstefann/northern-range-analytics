# `src/port_analytics/load/connection.py`

Module docstring (L1-4) states the standing rule this file exists to uphold: "Never holds a
connection string or credential as a literal -- those come from .env (gitignored) or the real
process environment." That claim is the thing to check against the code below.

---

## `build_connection_string` in src/port_analytics/load/connection.py (L27-51)

**Purpose:** Assembles the ODBC connection string used to reach the Azure SQL database, sourcing
server/database/username/password from the environment (optionally populated from a `.env` file)
and failing loudly if any required value is absent. Everything downstream that talks to the
database (`connect()`, L54-55, and transitively `cli.py`'s `run()`, cli.py L47) depends on this
returning a complete, correctly-shaped string or raising before any partial/garbage string is used.

**Inputs & Assumptions:**
- No parameters. Reads from `os.environ` (L29) after a `load_dotenv()` call (L28).
- Implicit input: the process environment at call time, and the contents of a `.env` file found by
  `python-dotenv`'s upward directory search — trusted in the sense that it's operator-controlled,
  but its *contents* (a password) are secret and must be handled as such downstream.
- `REQUIRED_ENV_VARS` (L15-20): fixed tuple of four names — `NORTHERN_RANGE_SQL_SERVER`,
  `_DATABASE`, `_USERNAME`, `_PASSWORD`. This is the sole enumeration of what's required; nothing
  validates the *shape* of these values (e.g. that `SERVER` doesn't itself contain `;` or other
  ODBC-connection-string metacharacters) before they're interpolated into the string at L44-47.
- Precondition depended on by every caller: if this function returns, all four values are non-empty
  strings. Established by the `missing` check at L30-35: `values` is built via `.get()` (returns
  `None` if absent) at L29, `missing` collects names where `not value` is true (L30) — this also
  catches present-but-empty-string env vars, not just unset ones — and L31-35 raises
  `MissingConnectionConfig` before any of the four names is dereferenced further. Because the raise
  happens unconditionally when `missing` is non-empty, there is no path past L35 with any value
  still `None`/empty; the assignments at L37-40 and the f-string at L42-51 are unreachable unless
  all four passed the check. This is a real invariant, not just an assumption, given the code as
  written.

**Outputs & Effects:**
- Returns a single ODBC connection-string (L42-51) containing the driver name, `SERVER=tcp:{server},1433`,
  `DATABASE={database}`, `UID={username}`, `PWD={password}` in cleartext, plus fixed
  `Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;` suffixes.
- Side effect: `load_dotenv()` (L28) mutates `os.environ` for the whole process for the remainder
  of its lifetime (see callee analysis below) — not scoped to this function.
- Raises `MissingConnectionConfig` (subclass of `RuntimeError`, L23-24) whose message (L33-34) lists
  only the **names** of missing variables, never their values — consistent with the module's
  no-literal-secrets intent. Since missing vars have no value by definition, there is nothing to
  leak here regardless.
- No logging, printing, or `typer.echo` anywhere in this function. The string built at L42-51 is
  returned, not written to any log, file, or console by this function itself.

**Block-by-Block:**

```python
# L28
load_dotenv()
```
- **What:** Loads variables from a `.env` file (if found) into `os.environ`, without overriding
  variables already set in the real process environment.
- **Why here:** Runs before any `os.environ.get()` call so that a developer's local `.env` is a
  transparent source for the same variables production would supply via real env vars.
- **Assumes:** `python-dotenv`'s `find_dotenv()` locates the right `.env` relative to *this file's*
  location (via stack-frame walking), not the process's current working directory — confirmed by
  the callee source (see Cross-Function Dependencies) and called out explicitly in the project's
  own test comment, tests/unit/test_connection.py L17-18.
- **Establishes:** After this line, `os.environ` reflects `.env` values for any of the four names
  not already set in the real environment (default `override=False`).
- **Depended on by:** L29's `os.environ.get()` calls, which is how `.env`-sourced secrets enter this
  function at all.

```python
# L29-35
values = {name: os.environ.get(name) for name in REQUIRED_ENV_VARS}
missing = [name for name, value in values.items() if not value]
if missing:
    raise MissingConnectionConfig(
        f"Missing required environment variable(s): {', '.join(missing)}. "
        "Set them in .env (see .env.example) or the process environment."
    )
```
- **What:** Reads all four required values, then fails loudly (naming only the missing keys) if any
  is absent or empty.
- **Why here:** Gate before any of the values is used to build a connection string, so a partially
  configured environment can't silently produce a malformed/incomplete `SERVER=`/`UID=`/`PWD=`
  fragment that `pyodbc.connect` would then fail on with a possibly more confusing driver-level
  error.
- **Assumes:** `os.environ.get` returns `None` for unset names (stdlib guarantee) and that an
  empty-string value is equally invalid as unset for all four fields — true for server/database/
  username, and arguably desirable but unverified as a real-world constraint for password (an
  intentionally empty password would also be rejected here, which is conservative, not permissive).
- **Establishes:** The invariant that every `values[name]` is truthy for the remainder of the
  function (see Inputs & Assumptions above).
- **Depended on by:** L37-40 and L42-51, which dereference `values[...]` without further checks.

```python
# L37-51
server = values["NORTHERN_RANGE_SQL_SERVER"]
database = values["NORTHERN_RANGE_SQL_DATABASE"]
username = values["NORTHERN_RANGE_SQL_USERNAME"]
password = values["NORTHERN_RANGE_SQL_PASSWORD"]

return (
    f"DRIVER={ODBC_DRIVER};"
    f"SERVER=tcp:{server},1433;"
    f"DATABASE={database};"
    f"UID={username};"
    f"PWD={password};"
    "Encrypt=yes;"
    "TrustServerCertificate=no;"
    "Connection Timeout=30;"
)
```
- **What:** Interpolates the four values directly into ODBC key=value pairs, joined with `;`, and
  fixes `Encrypt=yes`/`TrustServerCertificate=no` (server identity is verified, traffic encrypted)
  and a 30s connect timeout.
- **Why here:** Last step; only reached once the missing-var gate has passed.
- **Assumes:** None of `server`, `database`, `username`, `password` contains a literal `;` or `=`
  that would let it inject or terminate an early ODBC connection-string key (e.g. a password
  containing `;` would truncate the `PWD=` field and everything after it, potentially appending
  attacker/operator-controlled trailing keys if the value also contained `key=value;` sequences).
  Nothing in this function or its caller escapes, quotes, or rejects such characters — the only
  validation applied to any of the four values is the truthiness check at L30, not a shape/charset
  check. This is an unenforced assumption; L44-47 is where enforcement would have to sit if it were
  ever added.
- **Establishes:** The full connection string, including the password in cleartext, as an in-memory
  Python `str`. Nothing marks or wraps it to prevent it from being logged or printed by a caller —
  it is an ordinary string once returned.
- **Depended on by:** `connect()` (L55), which passes this string directly to `pyodbc.connect`.

**Cross-Function Dependencies:**
- Callee `load_dotenv` (external, source available at `.venv/Lib/site-packages/dotenv/main.py`):
  called with no arguments (L28), so `dotenv_path=None`, `stream=None`, `override=False`,
  `verbose=False` (dotenv/main.py L389-394). Because `dotenv_path` and `stream` are both `None`,
  `find_dotenv()` is invoked (dotenv/main.py L424-425), which walks upward from the directory
  containing *this module's own file* (via stack-frame inspection, dotenv/main.py L365-380) unless
  running interactively/under a debugger/frozen (L361-364) — this function does not run under those
  conditions in normal CLI use, so the search is rooted at `connection.py`'s directory, not the
  process cwd. `override=False` means: if a name is already set in the real process environment,
  the `.env` value for that name is *not* applied (dotenv/main.py L296-306 area, `resolve_variables`
  called with `override=self.override`, and `set_as_environment_variables` at L97-105 skips keys
  already in `os.environ` when `override` is false). This function depends on `load_dotenv` to make
  `.env`-sourced values visible via `os.environ.get` at L29, and depends on it to *not* clobber
  real environment variables that are already set. `verbose=False` (the default) means dotenv does
  not print/log anything about the file it loads or its values — no leak path through this callee's
  own logging in the current call. If `PYTHON_DOTENV_DISABLED` is truthy in the environment,
  `load_dotenv` returns `False` without reading any file (dotenv/main.py L418-422) and this function
  silently falls back to whatever `os.environ` already had — not a secret-leak path, but a
  behavior change this function does not detect or report.
- Callers: `connect()` (L55) is the only in-repo caller; it depends on this function to either
  return a complete, well-formed connection string or raise `MissingConnectionConfig` — there is no
  third outcome (partial string, empty string) reachable given the L30-35 gate. `tests/unit/test_connection.py`
  exercises both the raise path (L12-22) and the success path (L25-39), including asserting the raw
  `PWD=secret` substring is present in the returned string (test L38) — i.e. the test suite itself
  treats the cleartext password in the returned string as expected/intended, not incidental.
- Shared state: `os.environ`, mutated by `load_dotenv()` for the life of the process — any other
  code running later in the same process (including anything that logs the environment for
  debugging, e.g. crash handlers, error-reporting SDKs, or a `print(os.environ)` in unrelated code)
  would see the four secret values after this function has run once.
- Invariant couplings: The "never a literal in source" rule from the module docstring (L1-4) is
  upheld structurally — grep of this file shows no hardcoded server/user/password. But the module
  docstring makes no claim about the *string this function builds*, which does embed the password
  as a literal value in memory for the remainder of its lifetime (until garbage collected), and
  that string is handed unmodified to `connect()` below and, through it, to `pyodbc.connect`.

**Open Questions:**
- Does any error-reporting/telemetry/crash-handling code elsewhere in the project (not found in
  this file's direct call graph) log `os.environ` or exception locals on an unhandled exception?
  `cli.py`'s `run()` (cli.py L47) does not wrap `connect()` in a try/except, so any exception from
  `pyodbc.connect` propagates to Typer's default handler / an uncaught-exception traceback. Need to
  check whether Typer or the environment prints local variables (which would include
  `build_connection_string`'s local `password`) on an unhandled exception, or whether only the
  exception message and stack frames without locals are shown by default.

---

## `connect` in src/port_analytics/load/connection.py (L54-55)

**Purpose:** Thin wrapper that turns a freshly built connection string into a live
`pyodbc.Connection`. This is the sole point where the process actually opens a network connection
to Azure SQL; `cli.py`'s `run()` depends on it for a connection object usable as a context manager
(cli.py L47, `with connect() as conn:`).

**Inputs & Assumptions:**
- No parameters.
- Implicit input: same environment/`.env` dependency as `build_connection_string`, transitively.
- Precondition: none beyond what `build_connection_string` itself guarantees — this function adds
  no additional validation of its own.

**Outputs & Effects:**
- Returns whatever `pyodbc.connect(...)` returns on success: a `pyodbc.Connection` (per the type
  stub, pyodbc.pyi L985 area, and the declared return type at L54).
- External interaction: opens a real TCP/TLS connection to the Azure SQL server named in the
  connection string, authenticating with the SQL username/password.
- No logging, printing, or exception handling in this function — any exception raised by either
  `build_connection_string()` (i.e. `MissingConnectionConfig`) or `pyodbc.connect` (e.g.
  `pyodbc.Error`, `pyodbc.OperationalError` for auth/network failures) propagates unmodified to the
  caller. Neither the connection string nor the individual secret values are referenced again after
  being passed into `pyodbc.connect` at L55 — this function holds no separate local copy.

**Block-by-Block:**

```python
# L54-55
def connect() -> pyodbc.Connection:
    return pyodbc.connect(build_connection_string())
```
- **What:** Builds the connection string and immediately passes it to `pyodbc.connect`, returning
  the resulting connection object (or letting an exception propagate).
- **Why here:** Single-expression composition — no intermediate variable holds the connection
  string, so there's nothing in this function's own frame beyond the argument itself for a
  traceback/debugger inspecting local variables to expose (the string still exists as an argument
  value in the frame while `pyodbc.connect` executes, and in `build_connection_string`'s frame as
  `password`/local pieces until that frame returns).
- **Assumes:** `pyodbc.connect` does not, on failure, raise an exception whose `str()`/`args`
  include the full connection string (and thus the password) verbatim. This is an assumption about
  an external, closed-source dependency (see Cross-Function Dependencies) — nothing in this
  repository enforces or verifies it.
- **Establishes:** On success, a connection invariant the rest of `load/loader.py` depends on: the
  returned object is a live, usable `pyodbc.Connection` supporting the context-manager protocol
  (used at cli.py L47) and `.cursor()`/`.commit()` (used throughout `load/loader.py`).
- **Depended on by:** `cli.py`'s `run()` (cli.py L47-48), which uses the connection inside a `with`
  block to call `load_all(conn, rows, flags)`.

**Cross-Function Dependencies:**
- Callee `build_connection_string` (internal, L27-51): depended on to either return a complete
  connection string or raise before this function calls `pyodbc.connect` — see full analysis above.
  There is no path where `connect()` calls `pyodbc.connect` with a partial/malformed string due to
  missing env vars, only due to *malformed-but-present* values (the injection-shaped concern noted
  under `build_connection_string`'s block-by-block for L37-51).
- Callee `pyodbc.connect` (external, black box — compiled extension at
  `.venv/Lib/site-packages/pyodbc.cp313-win_amd64.pyd`, no Python source available; only a `.pyi`
  type stub exists, pyodbc.pyi, which describes the signature but not runtime behavior or exception
  message contents). This function depends on it for: (a) parsing the ODBC keyword string correctly
  per the `DRIVER=...;SERVER=...;...` syntax built by `build_connection_string`; (b) raising some
  subclass of `pyodbc.Error` on failure rather than returning a broken/partial connection object
  silently; (c) not embedding the raw connection string (including `PWD=...`) in exception text,
  logs, or any diagnostic output it produces. None of (a)-(c) is verified from this codebase — they
  are assumptions about a black-box dependency, not invariants established by code that can be read.
  The `.pyi` stub confirms only the public signature (`connect(connstring: Optional[str] = None,
  ...)`, pyodbc.pyi L985), not behavior.
- Callers: `cli.py`'s `run()` (cli.py L15, L47) is the only caller in the repository. It assumes
  `connect()` either returns a working connection or raises — there is no `try`/`except` around the
  call (cli.py L47), so any `pyodbc.Error` (including one whose message might echo connection
  parameters, per the open question above) reaches Typer's uncaught-exception path and, depending
  on Typer's/the shell's default behavior, is printed to stderr.
- Shared state: none beyond what `build_connection_string` already touches (`os.environ`). The
  README (L365-368) documents that this function and `cli.py`'s call to it are deliberately
  excluded from unit-test coverage (0%), verified instead "by the real, repeated runs against the
  live Azure SQL database" — i.e. this function's behavior on the failure paths (bad credentials,
  network failure, TLS failure) has no automated regression coverage in this repository.

**Open Questions:**
- Does `pyodbc.connect`, on an authentication failure against SQL Server / Azure SQL, include any
  part of the connection string (server name, username, or password) in the raised exception's
  message? This determines whether an uncaught exception surfacing at cli.py L47 (no try/except
  present) could print the password to stderr/console output via the default traceback. Needs
  behavioral testing against the actual driver/server, not just static inspection, since pyodbc is
  a compiled extension with no bundled source in this environment.
- Is there any process-level exception hook, logging integration, or crash reporter configured
  outside this file (e.g. in `cli.py`, a `sitecustomize.py`, or a packaging entry point) that could
  capture and persist/transmit the traceback — and with it, frame locals such as
  `build_connection_string`'s `password` variable or `connect`'s in-flight connection-string
  argument? Not found within `load/connection.py` or `cli.py` as read.
- `.env.example` (checked): contains only placeholder values — `NORTHERN_RANGE_SQL_SERVER=northern-range-sql-server.database.windows.net`,
  `NORTHERN_RANGE_SQL_DATABASE=NorthernRangeAnalytics`, `NORTHERN_RANGE_SQL_USERNAME=northernrangeadmin`,
  and `NORTHERN_RANGE_SQL_PASSWORD=` (empty) — no real credential present. Not itself a secret, and
  correctly not covered by `.gitignore` L2 (which lists only `.env`). Resolved, not an open question.
