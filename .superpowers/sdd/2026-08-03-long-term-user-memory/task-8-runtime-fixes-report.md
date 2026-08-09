# Task 8 Runtime Fixes Report

## Scope

This follow-up fixes runtime defects found at `dceb37b9`, updates the stale
`IR_04` golden case, and closes SQLite resources exposed by strict Python 3.13
warning checks. It does not change the deterministic command parser, owner-key
derivation, store uniqueness constraints, or alias authorization boundary.

## Root causes

1. Every specialist wrapper returned `memory_context`, while the Memory Agent
   also returned that key. Any fan-out such as Memory + Insights or Training +
   Meal therefore produced multiple writes to a LangGraph last-value channel in
   one superstep and raised `InvalidUpdateError`.
2. Initial, specialist, and post-mutation calls to `list_memories()` propagated
   repository exceptions. This aborted unrelated chat instead of failing open,
   and did not distinguish a required pre-mutation read failure from a
   post-commit refresh failure.
3. Golden case `IR_04` still described Context Governance as a durable-memory
   writer and had no Memory Agent route or memory-database assertion.
4. The first post-commit failure fix used same-ID message replacement: the
   reducer kept one finalized checkpoint message, but API and CLI had already
   consumed the provisional `memory` update and ignored `refresh_memories`.
   Evaluation consumed both raw updates and therefore graded duplicate output.
5. Several assertions and the existing business SQLite handler used
   `with sqlite3.connect(...)`. A connection context manager commits or rolls
   back but does not close the connection, so Python 3.13 later emitted
   `ResourceWarning` and `PytestUnraisableExceptionWarning` during garbage
   collection.

## TDD evidence

The first focused RED run selected 14 tests and produced 14 expected failures:

- Memory + Training/Meal/Insights and Training + Meal reproduced
  `InvalidUpdateError` with a compiled production graph and real SQLite.
- Chatter/Training/Meal/Insights propagated the simulated durable-memory read
  `OSError`.
- Remember/update/forget aborted at the failed initial load instead of returning
  a safe unavailable response.
- Specialist refresh and post-commit refresh failures propagated.
- `IR_04` still expected the old Context Governance route.

A separate two-specialist interrupt/resume RED reproduced the concurrent state
write again when both specialists resumed in the same superstep.

After the minimal implementation, the same focused set passed: 15 passed.

Independent review then exposed the streaming/reducer mismatch. A second RED
run covered a compiled graph with a real checkpoint plus the production API,
CLI, and evaluation consumers; all 5 selected cases failed for the expected
provisional/missing/duplicate response behavior. The corresponding GREEN run
passed all 5. Additional real-graph tests cover an initial unavailable load and
a composite Memory + interrupted specialist request through resume.

A fresh strict-warning review then reproduced 3 unclosed-database warnings in
the 4-case composite graph matrix. Tracemalloc attributed each allocation to
the test's direct SQLite assertion connection. The initial `-W error` gate
ended with a three-member unraisable-warning exception group. After explicit
connection closing, that gate passed 4 cases. Extending the same gate exposed
the identical lifecycle error in the CLI's real `init_db` path and its existing
test queries; the final branch-wide strict gate passed 266 cases.

## Implementation

- Added a single `refresh_memories` fan-in node reached from every routed Agent.
  LangGraph schedules it once after all active fan-out branches complete.
- Specialist wrappers still perform a fresh durable read for their local prompt,
  including after interrupt/resume, but return only their messages. Memory Agent
  returns only response/pending/commit metadata and no user message or
  `memory_context`. The fan-in node is therefore the only final snapshot writer
  and the only Memory response emitter; no reducer selects between stale and
  post-mutation values.
- Added explicit request snapshot availability state. Initial load failure uses
  an empty safe context so unrelated Agents continue. A routed mutation sees the
  unavailable snapshot and returns `长期记忆暂时不可用，请稍后重试。` without
  invoking interpretation or mutation.
- Specialist refresh failure falls back to the valid request snapshot. Final
  refresh failure keeps the prior snapshot, marks it unavailable, and, when a
  mutation already committed, returns a truthful success-plus-refresh-warning
  response without exposing the repository exception.
- Added `MemoryAgentResult.mutation_committed` so post-commit refresh handling is
  based on an explicit service result rather than response-text parsing.
- Added one shared response-node contract for API, CLI, and evaluation. They
  consume Training, Meal, Insights, Chatter, and finalized
  `refresh_memories` output, never a provisional Memory-node update. Success,
  clarification, fail-closed unavailable output, and post-commit warning are
  each emitted once. Composite interrupt requests defer the Memory reply until
  every branch resumes and the final refresh completes.
- Updated generator and checked-in JSONL for `IR_04` to route through Memory
  Agent and assert the exact owner/type/key/content row in the memory database;
  a real-SQLite regression rejects an unrelated row and accepts only the exact
  expected row.
- Wrapped direct SQLite connections in `contextlib.closing`, nesting the
  connection transaction context where writes require commit/rollback. This
  covers the production business handler and all branch-related memory,
  migration, API, evaluation, graph, and handler test connections. Deliberately
  long-lived WAL/concurrency connections remain explicitly closed by their
  callers.

## Verification

- Focused runtime/evaluation RED: 14 failed for the expected missing behavior.
- Multi-specialist resume RED: 1 failed with the expected
  `InvalidUpdateError`.
- Focused runtime/evaluation GREEN: 15 passed.
- First related memory/evaluation regression: 132 passed.
- Streaming consumer RED: 5 failed for the expected missing/duplicate output.
- Streaming consumer GREEN: 5 passed.
- Final focused API/CLI/evaluation/compiled-graph streaming set: 7 passed.
- Final related memory/API/evaluation regression: 130 passed.
- Original composite strict-warning RED: 4 functional passes followed by 3
  unclosed-database warnings and a failing `-W error` teardown.
- Original composite strict-warning GREEN: 4 passed.
- Task 8 strict-warning selection: 21 passed.
- Business handler + CLI strict-warning selection: 9 passed.
- Branch-wide SQLite/memory/API/evaluation `-W error` gate: 266 passed in
  78.33 seconds with no warnings.
- `make quality`: exit 0; Ruff clean; Black 58 files unchanged; MyPy 58 source
  files clean; Bandit found zero issues and emitted no warnings.
- `make verify`: 423 passed, 3 deselected in 82.45 seconds.
- `git diff --check`: exit 0.

## Independent verification findings

The first independent review passed the existing focused and full gates but
identified three gaps, all fixed before the streaming review:

- load failure was rendered as `(none stored)`, conflating unknown state with a
  confirmed-empty store; prompts now use an explicit unavailable marker and
  tests exercise the production specialist prompts;
- `IR_04` lacked a real-SQLite anti-broadening assertion; it now verifies the
  exact durable-memory row;
- architecture documentation did not show the explicit fan-in refresh; the
  graph and prose now document it.

The scoped follow-up found the same-ID replacement streaming defect described
above. That finding led to the single-emitter response design and production
API/CLI/evaluation regressions.

The final independent re-review reported **READY** with no new findings:

- focused compiled graph + real SQLite: 19 passed;
- API/CLI/evaluation streaming: 3 passed;
- `IR_04`/evaluation: 3 passed;
- context end-to-end: 2 passed;
- `make quality`: exit 0, 0 warnings;
- `make verify`: 423 passed, 3 deselected, 0 warnings;
- `git diff --check`: exit 0.

A later fresh final verifier used explicit `ResourceWarning`/`-W error` modes
and found the connection-lifecycle defect above, which the default pytest
warning policy had not displayed. After the closing fix, the same verifier's
scoped re-review reported **READY** with no findings:

- original matrix with `-W always::ResourceWarning`: 4 passed, zero warnings;
- original matrix with `-W error`: 4 passed, zero warnings;
- relevant branch-wide `-W error`: 266 passed, zero warnings;
- `make quality`: clean, 58 files unchanged;
- `make verify`: 423 passed, 3 deselected, zero warnings;
- all 89 production/test synchronous `sqlite3.connect` sites audited, with no
  warning suppression and every ordinary or deliberately long-lived connection
  closed by its owner.

## Residual risk

Repository failures are intentionally collapsed to generic user-facing
availability messages. The graph does not retry a failed initial snapshot
within the same mutation request, by design, so the user must issue the command
again after storage recovers. A mutation that commits before the final refresh
fails remains committed and is reported as such; the next request performs a
fresh load.
