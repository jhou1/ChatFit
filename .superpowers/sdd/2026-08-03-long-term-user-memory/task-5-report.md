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

No config-helper file outside the approved Task 5 file list was changed.

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
