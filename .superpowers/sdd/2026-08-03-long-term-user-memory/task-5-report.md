# Task 5 Report: Production Persistence Assembly

## Status

Implemented the approved Task 5 persistence slice in the isolated
`codex/long-term-memory` worktree. API, local CLI, and evaluation now construct
explicit durable-memory dependencies and supply a stable user identity beside
the conversation thread identity. Durable memory remains in its own SQLite
database and `/clear` only rotates short-term conversation state.

## Implementation

- Added `get_user_memory_db_path()` with `user-memory.db` default, recursive
  parent creation, and directory-target rejection.
- API lifespan constructs one configured `UserMemoryStore` and one
  `LLMMemoryInterpreter`, retains them on application state, and injects both
  into `make_agent_graph` alongside the existing independent business and
  checkpointer databases.
- Every `/chat` graph configuration contains the stable request `user_id` and
  the current `thread_id`. Memory Agent output is included in API responses.
- `/clear` remains scoped to `user_sessions`: it rotates only the user's thread
  ID and leaves the durable database untouched.
- CLI constructs the configured durable store and uses `local-cli` as the
  stable owner while retaining a random short-term thread ID.
- Evaluation constructs `test_eval.db` and `user-memory.db` inside each case's
  independent temporary directory. All turns in a case use `case.case_id` for
  both stable user identity and thread identity; separate cases receive
  separate SQLite files.
- Compose and `.env.example` configure
  `USER_MEMORY_DB_PATH=/app/data/user-memory.db`. `.gitignore` names the local
  database, WAL, and SHM files exactly and retains the existing `runtime-data/`
  ignore.

## Files

- `api.py`
- `main.py`
- `evaluation/runner.py`
- `tests/test_api.py`
- `.env.example`
- `docker-compose.yml`
- `.gitignore`
- `.superpowers/sdd/2026-08-03-long-term-user-memory/task-5-report.md`

The security hardening review added the shared
`agents/memory/config.py` SQLite-path validator plus the existing Bot and Bot
test files. This was the minimum scope needed to enforce one authenticated
trust boundary and one physical-file separation policy across all production
entry points.

## TDD Evidence

### RED

Command:

```bash
uv run pytest tests/test_api.py -k 'memory or configurable_user or clear' -v
```

Expected pre-implementation results:

```text
7 selected
6 failed, 1 passed
```

The three path tests failed because `get_user_memory_db_path` did not exist;
the full-lifespan test failed because startup exposed/injected no configured
memory store; CLI and evaluation failed because their graph calls supplied no
memory dependencies. The `/clear` characterization already passed, proving
the existing endpoint rotated only the session thread and retained a real
SQLite memory row.

The renamed identity test was also run directly before implementation:

```bash
uv run pytest tests/test_api.py -k 'configurable_user' -v
```

It failed with `KeyError: 'user_id'`, proving API graph configuration carried
only `thread_id`.

### GREEN

The exact approved focused command after implementation reported:

```text
8 passed, 15 deselected in 2.22s
```

The restart test uses two sequential real FastAPI lifespan contexts over the
same configured memory path. The first request executes the real Memory Agent
and real SQLite store; only the structured LLM runnable boundary is replaced
with a deterministic test double. After `/clear` and shutdown, a second startup
and new thread reload the exact memory for the same user while another user
cannot see it. Direct SQLite schema queries prove `user_memories` exists only
in the durable-memory database and not in the business or checkpointer files.

CLI/evaluation tests likewise keep `UserMemoryStore` and
`LLMMemoryInterpreter` real, patching only the external structured-model
boundary or graph execution recorder. No test accesses the network.

## Regression and Quality

Focused API/memory regression:

```bash
uv run pytest tests/test_api.py tests/test_memory_graph.py tests/test_user_memory_store.py -v
```

Result: `62 passed in 2.49s`.

Full verification:

```bash
make verify
```

Result: `277 passed, 3 deselected in 3.39s`; final line
`All verification checks passed.`

The first `make quality` run passed Ruff and Black, then mypy found one missing
type annotation on the test recorder's config list. After adding that test-only
annotation, the complete command was rerun from the beginning:

```bash
make quality
```

Final result: Ruff clean; Black unchanged across 54 files; mypy reported
`Success: no issues found in 54 source files`; Bandit reported no issues at any
severity or confidence; final line `All static check passed.`

`git diff --check` exited 0 with no output.

## Documentation Assessment

The new runtime setting is documented in `.env.example` and Compose. The
approved feature plan defers the complete user-facing and architecture
documentation for durable memory to Task 7; this Task 5 change therefore does
not modify `README.md`, `docs/index.html`, or `docs/architecture.md` beyond the
explicit runtime configuration files in its scope.

## Independent Verification

The first fresh verifier independently reproduced all green gates but found one
Important CLI assembly gap: `main.py` persisted Memory Agent operations while
discarding that node's confirmation or clarification output.

### Review fix round 1: render CLI memory responses

A regression changed the CLI graph double to emit a real graph-shaped
`memory` update and asserted its AI message appears in captured terminal
output. Before the production fix it failed because output contained only the
welcome and goodbye messages:

```text
1 failed in 2.51s
assert '已记住你的训练偏好。' in captured output
```

Adding `memory` to the CLI's existing user-visible node set made the exact test
pass: `1 passed in 2.04s`. This keeps the store/owner assertions in the same
test and proves an explicit remember/update/forget response is not silently
discarded.

The approved plan assigns complete durable-memory user and architecture
documentation to Task 7. The verifier therefore recorded stale README/site
content as a branch-level deferred documentation gate, not a Task 5 blocker;
no out-of-scope documentation file was changed.

After the fix and formatting, the local stable-snapshot gates reported focused
`8 passed, 15 deselected`, API/memory `62 passed`, full `277 passed, 3
deselected`, and a clean quality gate.

A second brand-new verifier reviewed the corrected snapshot and returned
**READY — Task 5 scope**, with no Critical, Important, Minor, warning, or code
finding. Its independent fresh evidence was:

- focused API: `8 passed, 15 deselected in 2.43s`;
- API/memory regression: `62 passed in 2.60s`;
- `make verify`: `277 passed, 3 deselected in 3.12s`;
- `make quality`: Ruff clean, Black unchanged across 54 files, mypy clean
  across 54 files, Bandit zero issues, and static checks passed;
- `git diff --check`: exit 0 with no output;
- pre/post status and diff stat identical, proving the verifier made no files
  changes.

The verifier confirmed every path, dependency injection, identity, clear,
restart, user-isolation, CLI-output, evaluation-isolation, and runtime-config
requirement. It also confirmed the report is intentionally ignored by
`.superpowers/sdd/.gitignore` and therefore requires force-add for the single
Task 5 commit.

## Security review fix round 1

The post-implementation security review identified four concrete boundary
issues. All four were reproduced by new tests before changing production code.

### Trusted API identity

`/chat` and `/clear` now require `Authorization: Bearer <CHATFIT_API_TOKEN>`.
The configured token is mandatory, compared with `secrets.compare_digest`,
and never included in logs or error details. A missing or malformed header
returns 401 with a Bearer challenge; an incorrect token returns 403. Both
checks run before session lookup, rotation, graph access, or durable-memory
access. Every Bot chat and clear request goes through centralized helpers that
attach the same header. Compose requires the shared setting for both services,
and `.env.example` instructs operators to generate an independent random
secret.

RED: four authenticated-boundary cases returned 200 and allowed access.
GREEN: all four return 401/403 as specified, with the victim's session and real
SQLite memory row unchanged and with no token value captured in logs.

### Normalized stable identity

`ChatRequest.user_id` now strips surrounding whitespace, requires at least one
character, and permits at most 128 characters. FastAPI returns 422 before the
endpoint for blank or overlong identities, and the normalized value is the
only value used for both session and durable-memory ownership.

RED: the four invalid chat/clear cases returned 200, while whitespace variants
created two user entries. GREEN: all invalid cases return 422 without session
or graph access, and both normalized requests use the same owner and thread.

### Physical SQLite separation

`agents/memory/config.py` provides a shared canonical SQLite-file resolver and
separation validator. It rejects `:memory:`, `file::memory:` and URI
`mode=memory` targets; expands `~`; resolves relative, absolute, and symlinked
paths; rejects directory targets; and also detects existing hard links via
`samefile`. API startup validates the business, checkpoint, and memory files
pairwise before constructing any database, store, vector, or graph dependency.
The CLI applies the same rule to business and durable-memory files.

RED: all seven memory-target and collision cases crossed the required boundary.
GREEN: all seven fail with a clear physical-file error before dependency
construction.

### Test-state isolation

An autouse API fixture snapshots and restores the complete FastAPI state
dictionary, user-session mapping, and API-token environment value for every
test. This prevents closed graphs and mutated globals from leaking between
lifespans or making outcomes order-dependent.

### Independent-review startup ordering fixes

The first verifier of this security snapshot found two Important operational
ordering gaps. New regressions were added before the fixes. All three API
collision variants initially reached Langfuse initialization and the Bot
without `CHATFIT_API_TOKEN` initially reached Telegram construction, producing
four expected failures. API path separation now runs immediately after token
validation, before Langfuse or any other dependency is initialized. Bot startup
now validates the API credential before constructing the Telegram application
or beginning polling. The exact four-test command then passed 4/4.

### Final local gates after security fixes

Focused regressions:

```text
tests/test_api.py + tests/test_bot.py: 86 passed
tests/test_memory_graph.py + tests/test_user_memory_store.py: 39 passed
```

Repository verification:

```text
make verify: 295 passed, 3 deselected; all verification checks passed
make quality: Ruff clean; Black unchanged across 55 files; mypy clean across
55 source files; Bandit reported zero issues; all static checks passed
git diff --check: exit 0 with no output
```

### Final independent security verification

A second brand-new verifier reviewed the corrected stable snapshot and returned
**READY — Task 5 security fix**, with no Critical, Important, Minor, warning,
or code finding. Its independent evidence was:

- focused API, Bot, and memory suites: `125 passed`;
- order-sensitivity probes: `2 passed`;
- `make verify`: `295 passed, 3 deselected`;
- `make quality`: Ruff, Black, mypy, and Bandit clean with zero findings;
- `git diff --check`: clean;
- manual probes confirming all required 401/403 outcomes, accepted valid auth,
  startup fail-closed ordering, memory-mode and directory rejection, and
  hard-link collision detection;
- identical pre/post HEAD, status, untracked-file list, and diff stat, proving
  the verifier made no changes.

The verifier confirmed the RED/GREEN evidence against baseline `9edd83e`. It
also recorded the durable-memory/auth README and site documentation as the
explicitly approved Task 7 follow-up, not a blocker for this scoped security
fix. One initial command encountered only the sandbox's uv-cache permission
boundary; the identical authorized command completed successfully.

## Security review fix round 2

The original reviewer rechecked the first security fix and identified two
remaining parsing boundaries. Both were reproduced independently before the
production changes.

### Single Bearer scheme and credential

The authorization parser now accepts exactly one case-insensitive `Bearer`
scheme and one RFC-style token68 credential. It inspects the complete raw
header list, requires exactly one Authorization field, permits only HTTP spaces
between scheme and credential, and validates the full credential alphabet.
Missing or duplicate authorization, another scheme, a bare scheme, quoted or
comma-delimited credentials, non-HTTP whitespace, or any extra credential
returns 401 with
`WWW-Authenticate: Bearer`. Only a grammatically valid single credential that
fails `secrets.compare_digest` returns 403 without the challenge. Authentication
continues to run before session, graph, or durable-memory access.

The expanded real-route regression crosses both `/chat` and `/clear`, checks
the response contract, keeps the victim session and SQLite memory row intact,
and proves neither configured nor supplied secrets appear in captured logs.
Against commit `44a19f7`, the focused test produced the expected `4 failed, 8
passed`: both extra-credential variants were misclassified as 403. After the
parser fix, all `12 passed`.

The first round-2 verifier then challenged forms outside the initial matrix and
found that whitespace splitting still accepted NBSP/HTAB separators and the
first of two Authorization fields, while comma and quoted credentials were
misclassified as 403. Those five malformed forms were added across both routes
before the next production change. The expanded test produced the expected
`10 failed, 12 passed`; switching to raw-header cardinality plus a token68
`fullmatch` made all `22 passed`.

### Reject every SQLite `file:` URI

The shared resolver now rejects every explicit case-insensitive `file:` scheme
before constructing a `Path` or creating a parent directory. This is required
because the production SQLite connections do not enable `uri=True`; accepting
such input would otherwise create literal colon/question-mark paths instead of
opening the intended database. Plain filesystem paths remain unaffected,
including paths whose later components contain a colon.

API coverage includes `:memory:`, memory-mode URIs, `file:/tmp/...mode=rwc`, a
mixed-case `file:user.db`, and a mixed-case URI with a percent-encoded path.
CLI coverage verifies both ordinary and percent-encoded file URIs fail before
business/vector/store setup. All cases assert no literal `file:` directory is
created. Against `44a19f7`, the focused command produced the expected `5
failed, 3 passed`; after the shared resolver fix it reported `8 passed`.

### Round-2 local gates

```text
API + Bot + user-memory store: 126 passed
make verify: 319 passed, 3 deselected; all verification checks passed
make quality: Ruff clean; Black unchanged across 55 files; mypy clean across
55 source files; Bandit reported zero issues; all static checks passed
git diff --check: exit 0 with no output
```

### Round-2 final independent verification

After the first verifier's strict-grammar finding was fixed, a second
brand-new verifier returned **READY — Task 5 security fix round 2**, with no
Critical, Important, Minor, warning, or code finding. Its evidence was:

- focused API, Bot, and user-memory store suite: `126 passed`;
- `make verify`: `319 passed, 3 deselected`;
- `make quality`: Ruff clean, Black unchanged across 55 files, mypy clean
  across 55 source files, and Bandit zero issues;
- `git diff --check`: exit 0 with no output;
- raw ASGI probes proving duplicate Authorization fields, invalid bytes, NBSP,
  and HTAB separators return 401 plus Bearer challenge on both routes, while a
  valid token68 mismatch returns 403 and mixed-case Bearer with edge OWS works;
- API/CLI probes rejecting six explicit, mixed-case, and percent-encoded
  `file:` URI forms before artifacts or dependencies while accepting an
  ordinary colon-bearing filesystem path;
- identical pre/post HEAD, status, cached diff, untracked-file list, and source
  diff SHA-256, proving the verifier made no changes.

The verifier recorded the complete README, architecture, and site updates as
the explicitly approved Task 7 documentation follow-up rather than a scoped
round-2 blocker.
