# Whole-branch fix-wave report

Date: 2026-08-09 (Asia/Shanghai)

Reviewed branch: `codex/long-term-memory`

Review base: `7463fcb`

Implementation commits: `460881c`, `a9894a4`

## Outcome

The three Important findings and one Minor finding from the final whole-branch
review are addressed. The implementation preserves owner isolation, exact
payloads, fail-closed mutation behavior, and telemetry fail-open behavior.

## Findings addressed

### 1. Exact owned aliases route deterministically

The Supervisor now uses the shared explicit-command parser plus a fresh,
owner-scoped store resolution before routing an update or forget. Exactly one
eligible durable-memory match prepends `memory_agent` even when the LLM router
chooses chatter or times out. Zero matches, multiple matches, repository
resolution failures, other-owner rows, and a forged resolution flag cannot
authorize a write. Generic meal/training record edits backed by `MemoryType.OTHER`
remain specialist-mediated.

The graph regression performs remember, alias update, cross-thread reload, and
forget against real SQLite. It proves the alias update preserves the row ID,
increments the version once, changes the exact content, and that forget removes
the memory and its aliases.

### 2. Migration never overwrites different content

`UserMemoryStore.reconcile_exact_in_transaction` now rejects a canonical row
whose stored content differs from the approved migration value before metadata
or aliases are touched. Same-content reconciliation can repair display metadata
and aliases without changing content, row ID, or version.

Apply performs the check inside its existing write transaction. Dry-run performs
an immutable, read-only compatibility check for a checkpointed existing
destination. Both modes return a safe nonzero conflict for different existing
content or an alias owned by another row, and preserve the destination memory
rows, aliases, and SQLite file family. If WAL, SHM, or journal sidecars prevent a
complete immutable snapshot, dry-run returns a safe nonzero result without
opening SQLite or changing any destination-family member.

### 3. Pending completions stay operation- and source-bound

Every pending reply is freshly interpreted and must keep the same operation.
Fixed memory type, canonical/display identity, and captured target scope cannot
drift. A target-selection reply must freshly name exactly one captured candidate.
For missing remember/update content, the fresh interpreter content must equal
the exact user continuation; a bare confirmation or an unrelated ordinary
message leaves the pending action and database unchanged.

Targeted pending merges no longer accept fresh aliases or identity fields.
Captured versions are still rechecked immediately before update/forget.

### 4. Privacy-safe mutation observability

The shared `MemoryAgent.handle` boundary emits one `memory.mutation` event for
explicit remember/update/forget attempts. Attributes are limited to:

- `memory.operation`
- `memory.result`
- `memory.owner_id`
- optional `memory.id`

Results are `committed`, `unchanged`, `conflict`, `clarify`, `failed`, or
`forgotten`. Owner and memory identifiers are keyed HMAC fingerprints. Raw user
IDs, owner keys, memory UUIDs, content, aliases, user messages, clarification
questions, and exception text are excluded. Sink exceptions remain fail-open;
the user response and committed SQLite transaction are unchanged.

## TDD evidence

Each finding was reproduced before its implementation:

- Alias routing RED: 2 parametrized graph cases failed because chatter/timeout
  still bypassed the Memory Agent. Focused GREEN: 5 passed for alias routing,
  forged resolution, and generic business routing; 2 passed for multiple-target
  and resolution-failure fail-closed cases.
- Pending completion RED: 4 cases wrote an unrelated continuation or allowed
  target/type drift. Focused GREEN: 4 passed; the full Memory Agent file later
  passed 57 tests before the observability cases were added.
- Migration RED: 4 cases exposed dry-run success, apply overwrite, a version
  increment during metadata repair, and the missing store conflict. Focused
  GREEN: 4 passed.
- Independent-verifier follow-up RED: 2 of 3 focused cases failed because an
  active-WAL canonical conflict and a checkpointed alias collision returned zero
  in dry-run. Focused GREEN after fail-closed parity: 3 passed, 57 deselected in
  6.88s; both subprocess regressions preserve rows, aliases, and file-family
  snapshots.
- Observability RED: 2 cases observed zero events and zero sink calls. Focused
  GREEN: 2 passed, 57 deselected in 2.01s.

## Verification evidence

- `.venv/bin/pytest -q tests/test_memory_agent.py tests/test_observability.py`:
  64 passed in 1.96s.
- `.venv/bin/pytest -q tests/test_user_memory_store.py tests/test_memory_migration.py`:
  77 passed in 77.44s after the verifier follow-up.
- `.venv/bin/pytest -q -W error tests/test_memory_agent.py tests/test_memory_graph.py tests/test_memory_migration.py tests/test_user_memory_store.py tests/test_observability.py`:
  187 passed in 82.92s, with zero warnings.
- `make quality`: Ruff clean, Black clean (58 files unchanged), MyPy clean
  (58 source files), Bandit zero issues.
- `make verify` (non-PTY): 435 passed, 3 deselected in 89.54s.
- `.venv/bin/pytest -q tests/test_documentation.py`: 17 passed in 5.17s after
  the contract documentation update.
- `git diff --check`: clean.

An initial `make verify` invocation incorrectly allocated an outer PTY. Its
interactive-zsh documentation subprocess blocked until interrupted. A verbose,
non-PTY run of `tests/test_documentation.py` passed all 17 cases in 4.99s, and
the required non-PTY `make verify` then passed in 82.35s. This was an invocation
artifact, not a product or test regression.

## Documentation

`README.md`, `docs/architecture.md`, and `docs/observability.md` now describe
deterministic exact-alias routing, source-bound clarification, non-overwriting
migration reconciliation, the mutation event schema, privacy exclusions, and
sink fail-open behavior. `docs/index.html` makes no memory behavior claim and
does not require a change.

## Residual constraints and risk

- Generic business edits remain intentionally specialist-mediated when an exact
  colliding durable row is typed `other`; this prevents ordinary meal/training
  record edits from becoming memory writes.
- Dry-run fails closed rather than attempting to interpret an active destination
  SQLite file family. Operators must checkpoint and close writers before retrying
  the compatibility check or apply.
- Telemetry is intentionally best-effort. A broken sink produces a sanitized
  warning and cannot be used as proof that the durable transaction failed.

## Independent verification

Round 1 returned NOT READY after reproducing dry-run success for both an
active-WAL canonical conflict and a checkpointed alias collision. Commit
`a9894a4` addresses both with fail-closed sidecar handling and complete alias
compatibility checks. A fresh independent re-verification of the follow-up and
all required gates is pending.
