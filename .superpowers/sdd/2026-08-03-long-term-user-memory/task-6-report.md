# Task 6 Report: Explicit Legacy Memory Migration

## Status

Implemented the approved Task 6 migration slice in the isolated
`codex/long-term-memory` worktree. The deterministic CLI scans only explicit
legacy notes through a read-only SQLite connection, reports candidate counts
without printing note bodies, and optionally creates or updates the canonical
`training_template:213` memory through the production store.

## Implementation

- Added `scripts/migrate_explicit_memories.py` with required `--source-db`,
  `--memory-db`, and `--user-id` options plus opt-in `--apply`.
- Resolves filesystem paths without creating directories during validation;
  rejects missing/directory/non-SQLite sources, directory/non-SQLite
  destinations, SQLite URI/memory values, and identical/symlink/hard-link
  source and destination files.
- Opens the source exclusively with a `file:` URI and `mode=ro`, additionally
  enables `PRAGMA query_only`, and runs one deterministic `SELECT DISTINCT`
  over `training_sessions.note` for `记住` or `需要记忆` markers.
- Uses one anchored, full-note regular expression. Only the complete explicit
  legacy `2-1-3 是一个训练模板……它代表<definition>` form is recognized;
  ordinary 213 records, missing markers, incomplete forms, and other keys are
  not migrated.
- Reports recognized/unrecognized counts and the safe canonical identity, but
  never emits the original notes or extracted definition.
- Dry-run does not construct the production store and therefore creates or
  writes no destination files or directories.
- Apply constructs `NewUserMemory` with type `training_template`, canonical
  key `213`, display name `2-1-3`, the complete extracted definition, and
  aliases `213`, `2-1-3`, and `壶铃213`.
- New and identical values use `UserMemoryStore.remember`; different existing
  content for the same owner/type/key uses `UserMemoryStore.update`, retaining
  the ID and incrementing the version. Alias or multi-definition conflicts
  return nonzero without duplicates or partial row changes.

## Files

- `scripts/migrate_explicit_memories.py`
- `tests/test_memory_migration.py`
- `.superpowers/sdd/2026-08-03-long-term-user-memory/task-6-report.md`

No package `__init__.py` was needed: the CLI is executed as the existing
namespace package module `python -m scripts.migrate_explicit_memories`.

## TDD Evidence

### RED

The initial focused command was run before production code existed:

```bash
uv run pytest tests/test_memory_migration.py -v
```

After tightening two false-green invalid-input assertions, all eight initial tests
failed for the expected missing module or missing specific error contract.
This proved the tests could detect absence of every requested behavior before
implementation.

### GREEN

The same focused command after the minimal implementation reported:

```text
9 passed in 30.03s
```

The tests use real temporary SQLite files and a real `UserMemoryStore`; no LLM,
mock store, or network boundary is involved. They assert deterministic output,
candidate privacy, zero dry-run writes, full-definition preservation, canonical
aliases, second-apply stable ID, existing-content in-place update, conflict
rollback, invalid path/file handling, and byte/schema/row-identical source
state.

## Regression and Quality

Focused migration plus production store regression:

```bash
uv run pytest tests/test_memory_migration.py tests/test_user_memory_store.py -v
```

Result: `25 passed in 30.88s`.

The first targeted lint pass found one unused import and Black requested
formatting. Both findings were corrected, then the complete quality gate was
rerun from the beginning:

```bash
make quality
```

Final result: Ruff clean; Black unchanged across 57 files; mypy reported
`Success: no issues found in 57 source files`; Bandit reported zero issues at
all severities/confidences; final line `All static check passed.`

Full verification:

```bash
make verify
```

Result: `328 passed, 3 deselected in 33.54s`; final line
`All verification checks passed.`

`git diff --check` exited 0 with no output.

## Documentation Assessment

The approved plan assigns user-facing migration commands and the durable
memory architecture documentation to Task 7. Task 6 therefore adds no README
or `docs/index.html` copy outside its two implementation/test files; this
report records the behavior for Task 7's documentation work.

## Independent Verification

The fresh verifier's first pass reproduced the focused and store suites without
warnings, then found one Important metadata-idempotency gap: an existing
canonical row with identical content but only the `213` alias caused
`remember` to return unchanged, leaving the required `2-1-3` and `壶铃213`
aliases absent.

A new real-SQLite regression first failed with `status=unchanged`. The minimal
fix now treats content, display name, and the exact alias set as the complete
migration value: only a fully identical row calls `remember` and remains
unchanged; stale content or metadata calls production `update`, preserving the
ID and replacing aliases. The regression then passed, a second apply returned
unchanged, focused migration reported 9 passed, migration/store reported 25
passed, and both complete local gates passed with the updated totals above.

The verifier also independently exercised symlink and hard-link destination
aliases. Both returned nonzero with the safe distinct-file error, and the
source SHA-256 remained unchanged. No files were modified by the verifier.

The verifier's final verdict was **READY — Task 6 corrected snapshot**, with no
remaining Critical, Important, or Minor findings. Its fresh evidence was:

- focused migration: `9 passed in 30.05s`;
- migration plus production store: `25 passed in 29.70s`;
- `make verify`: `328 passed, 3 deselected in 32.35s`;
- `make quality`: Ruff, Black, mypy, and Bandit clean with zero issues or
  warnings;
- tracked and untracked whitespace checks: exit 0 with no output;
- pre/post status and hashes identical for the script, test, and ignored report.

One verifier-written supplemental command initially had an f-string escaping
`SyntaxError`; it did not execute product code or modify the repository. The
verifier corrected and freshly reran that experiment with exit 0, confirming
zero dry-run parent creation, private-output redaction, stable-ID metadata
repair and second-apply idempotency, symlink/hard-link rejection, and unchanged
source SHA-256.
