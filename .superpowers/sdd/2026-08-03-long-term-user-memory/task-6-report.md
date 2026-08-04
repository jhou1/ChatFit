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
- Refuses nonempty source WAL/rollback journals, snapshots source main/WAL/SHM
  metadata and SHA-256, then opens the source exclusively with a `file:` URI,
  `mode=ro&immutable=1`, and `PRAGMA query_only`. A post-scan snapshot check
  proves the source family did not change.
- Runs one deterministic `SELECT DISTINCT` over `training_sessions.note` for
  marker-related candidates. BLOB candidates are counted as unrecognized
  without decoding or printing them.
- Uses one anchored, full-note regular expression. Only the complete explicit
  legacy `2-1-3 是一个训练模板（你需要记忆一下），它代表<definition>` form is
  recognized. The approved preamble grammar is enumerated rather than inferred
  from positive substrings or negative blacklists. Negative/indirect markers
  and definitions missing any required component/count/hand/minute are
  unrecognized; ordinary 213 records, missing markers, and other keys are not
  migrated. Approved whitespace, Chinese or English parentheses/punctuation,
  and definition case variations remain accepted.
- Reports recognized/unrecognized counts and the safe canonical identity, but
  never emits the original notes or extracted definition.
- Dry-run validates an existing destination only through raw magic bytes and
  stat; it never opens the destination through SQLite, creates SHM, constructs
  the production store, or creates destination files/directories.
- Apply constructs `NewUserMemory` with type `training_template`, canonical
  key `213`, display name `2-1-3`, the complete extracted definition, and
  aliases `213`, `2-1-3`, and `壶铃213`.
- Apply refuses destination WAL/SHM state that is not cleanly checkpointed and
  closed. It opens and holds the verified destination parent directory with
  `O_DIRECTORY|O_NOFOLLOW`; unsupported dirfd/follow-symlink platforms fail
  closed. For a missing destination, private staging creation, atomic
  no-replace hard-link installation, rollback, and cleanup use relative dirfd
  operations in that physical directory. The staging file's creation fd fixes
  its initial inode and complete state before close; it is then reopened
  `O_NOFOLLOW` relative to the held parent and kept open while SQLite uses
  `mode=rw`. Parent/path/inode/source-distinct identity is checked immediately
  after SQLite connect and before any SQL, then around commit. The user-visible
  parent identity is checked before and after commit, so rename-plus-symlink
  replacement cannot redirect writes or leak staging files.
- An existing destination is never copied or exchanged. It is reconciled in
  place through one SQLite connection and one `BEGIN IMMEDIATE` transaction,
  so SQLite serializes concurrent writers. The connection-scoped production
  store operation creates, exactly updates, or leaves unchanged the canonical
  value while retaining IDs and incrementing versions only on updates. The
  destination is first opened relative to the held parent with `O_NOFOLLOW` and
  matched to the snapshot/source inode before SQLite connect. Parent/path/inode
  identity is checked again immediately after connect and before any SQL or
  `BEGIN`, then after transaction binding, before commit, and after commit.
  Path replacement, alias conflict, stale state, or source changes return
  nonzero without creating sidecars through a source hard-link alias.

## Files

- `scripts/migrate_explicit_memories.py`
- `agents/memory/store.py`
- `tests/test_memory_migration.py`
- `tests/test_user_memory_store.py`
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

The same focused command after the final review fixes reported:

```text
53 passed
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

Final results: migration `53 passed`; production store `17 passed`; combined
suite `70 passed in 68.08s`.

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

Final result: `373 passed, 3 deselected in 69.13s`; final line
`All verification checks passed.`

`git diff --check` exited 0 with no output.

## Documentation Assessment

The approved plan assigns user-facing migration commands and the durable
memory architecture documentation to Task 7. Task 6 therefore adds no README
or `docs/index.html` copy. This is an approved branch-level deferred
documentation gate, and this report records the behavior for Task 7's
documentation work.

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

## Review Fix Round 1

A later Task 6 review returned NOT READY with four Important findings and one
Minor finding. All were reproduced before production changes. The combined
focused RED selected 15 new cases and reported 15 failures:

- negative markers and partial definitions were recognized or omitted from the
  unrecognized count;
- read-only source and dry-run destination SQLite opens created SHM, and active
  WAL content was not rejected;
- hard-link/symlink destination replacement after validation wrote memory
  tables into the source;
- a concurrent destination created during reconcile was silently merged or
  overwritten instead of making the staging commit fail;
- stale store errors, BLOB notes, and `expanduser` failures escaped as private
  tracebacks.

The fixes were applied one behavior at a time. Recognition/BLOB tests first
reported 8 passing, then WAL/path/concurrency/stale/path-safety tests reported
8 passing. A separate staging-copy race regression then failed because the
temporary database remained after the destination snapshot changed; cleanup
inside staging creation made the same test pass while preserving the concurrent
destination row. A second cleanup RED forced `os.close` to fail immediately
after `mkstemp`; assigning the staging path before descriptor close prevents
that earliest failure path from leaking the temporary file. The complete
focused file now has 32 passing tests.

Real WAL fixtures keep a connection open, remove SHM, and snapshot existence,
size, mtime, and SHA-256 before invoking the CLI. Source active WAL returns
nonzero without creating SHM; destination active WAL is read-only during
dry-run and rejected without change during apply. Path-race tests swap the
validated destination to real source hard links and symlinks, and assert source
bytes/schema remain unchanged with no memory tables. Concurrent destination
creation and copy-time mutation tests assert the external write survives and
all staging artifacts are absent.

After the fixes, local gates were fresh and clean: focused migration `32
passed`, production store `16 passed`, full verification `351 passed, 3
deselected`, and Ruff/Black/mypy/Bandit reported zero issues or warnings.

The next independent review of this snapshot produced the round-2 findings
recorded below.

## Review Fix Round 2

The next independent verifier found two additional Important gaps. Both were
captured with real-SQLite regression tests before the final production change:

- the permissive component matcher accepted contradictory duplicate fragments
  when the correct fragments appeared later in the same definition;
- a destination created or updated after the final snapshot check but before
  `os.replace` was silently overwritten.

The four focused regressions first reported four expected failures. Definition
recognition now uses an anchored grammar that consumes exactly the approved
eight components in order, allowing only the documented whitespace,
punctuation, case, and conjunction variants while preserving the original
definition text. Extra or contradictory components are unrecognized.

The first round-2 commit-window correction used atomic link/no-replace for
missing destinations and platform atomic main-file exchange for existing
destinations. This design passed its initial main-file races but was later
superseded by Review Fix Round 3 because a main-only exchange cannot safely
reconcile SQLite WAL state.

The round-2 main-file regressions passed at that snapshot. The unsupported
exchange and rollback-recovery tests were removed when existing-destination
main-file exchange itself was removed in round 3.

## Review Fix Round 3

A fresh independent verifier found an Important WAL-sidecar CAS hole. With an
existing database configured for persistent WAL but initially free of
sidecars, a committed update injected after the final snapshot created WAL/SHM
without changing the main-file hash. Main-file exchange missed those sidecars,
returned `status=created`, and the final database retained only the concurrent
update: the reported 213 migration was absent.

A real persistent-WAL regression reproduced that exact false success before
production changes. Existing destinations now avoid staging and filesystem
exchange entirely. The migration opens one SQLite connection, executes `BEGIN
IMMEDIATE`, validates that the destination path still resolves to the expected
device/inode and remains distinct from the unchanged source, and calls the
production store's new connection-scoped exact reconcile. It repeats identity
checks before and after commit. SQLite therefore serializes writers, and the
regression now ends with both the concurrent external update and canonical 213
memory present.

The production store method creates, exactly updates, or leaves unchanged a
memory within the caller-owned transaction; it never commits or rolls back.
Its focused test proves caller rollback, creation, exact metadata/alias repair,
stable ID, version increment, and idempotency. Two migration tests replace the
destination path after the transaction has bound its database connection, once
with a source hard link and once with an unrelated SQLite database. Both return
nonzero, leave source/replacement rows untouched, and roll back the original
database mutation. Missing destinations continue to use private staging plus
atomic link/no-replace.

A final focused privacy RED exposed that the new existing-destination branch
could let a store alias conflict escape with a traceback. Global migration
error mapping now converts store conflict/stale exceptions to the same safe
nonzero conflict message used by staging. The alias-conflict test additionally
asserts that stderr contains no traceback while the transaction leaves all
preexisting rows and aliases unchanged.

The next independent pass found that both commit linearization paths performed
their last source-family check immediately before committing, but not after.
Two injected source writes demonstrated false success after an existing
destination transaction commit and around a missing destination's atomic link.
Both regressions first failed, then passed after adding a final full
main/WAL/SHM/journal source snapshot check after each commit operation. A
source change before the command returns now produces a safe nonzero result
instead of `status=created`.

Fresh local evidence after round 3 is migration `40 passed in 65.60s`, store
`17 passed in 1.51s`, full verification `360 passed, 3 deselected in 65.71s`,
and a clean quality gate across Ruff, Black, mypy, and Bandit.

## Review Fix Round 4: Marker Grammar and Parent Anchoring

The original reviewer returned two further Important findings. The first was a
recognition-policy flaw: arbitrary positive substrings plus a finite negative
blacklist accepted indirect or negative phrases. Real-SQLite parameterized
tests showed three exact false positives (`不需要记忆`, `不必记住`, and
`没有让你记住`); the other three required negatives were already rejected.
Recognition now anchors the complete approved historical preamble grammar.
All six negative/indirect forms are unrecognized, while Chinese/English
parentheses and reasonable spacing preserve the exact approved definition.

The second finding was a filesystem parent TOCTOU. Three RED tests renamed the
validated destination parent and replaced its user path with a symlink after
validation, after staging, and immediately before install. The former path-only
implementation either installed into the replacement directory or leaked the
staging database in the moved physical directory. A fourth RED swapped an
existing destination to a hard link of a clean persistent-WAL source; SQLite
created alias WAL/SHM before the old post-`BEGIN` identity check.

The migration now holds the verified physical parent dirfd for its full
lifetime. Destination snapshots and all missing-target file mutations are
relative to that fd, and cleanup remains anchored even after parent rename.
Existing targets are opened `O_NOFOLLOW` relative to the fd and fully matched
to the expected main-file snapshot and source identity before SQLite connect;
the path is revalidated after connect before the first PRAGMA or transaction.
The existing connection uses SQLite URI `mode=rw`, so removal after verified
open cannot implicitly create a replacement database.
Darwin resolves the held directory through `F_GETPATH`, Linux through
`/proc/self/fd`; missing flags, relative-operation support, or platform path
resolution fails closed.

An additional narrow RED removed the existing destination immediately after
the verified dirfd-relative open. Plain `sqlite3.connect(path)` recreated an
empty file before identity validation; `mode=rw` now fails without creating a
main file or sidecars.

Fresh evidence after this review fix is migration `48 passed in 68.28s`, store
`17 passed in 1.44s`, full verification `368 passed, 3 deselected in 70.30s`,
and a clean quality gate across Ruff, Black, mypy, and Bandit. All three parent
swap regressions leave both replacement and moved directories without an
installed destination or staging artifact; the clean-WAL source hard-link
regression leaves source hashes/mtimes unchanged and creates no alias WAL/SHM.

## Review Fix Round 5: Staging Identity Before SQLite

The next fresh verifier returned NOT READY with two missing-destination
findings. Replacing the private staging entry with a source hard link
immediately before the old path-based `_apply_memory` call caused SQLite to
create the memory schema in the source before the later commit check rejected
the migration. Renaming the validated parent and replacing its user path with
a symlink at the same point redirected the cached SQLite staging path into the
replacement directory and leaked a populated staging database there.

Both attacks were captured as real-SQLite RED regressions. Missing-destination
apply now mirrors the verified existing-destination binding: it validates the
staging path's complete creation state, opens it `O_NOFOLLOW` relative to the
held parent dirfd, compares the opened fd inode to the creation inode and the
source inode, derives the current physical held-parent path only for a
`mode=rw` SQLite connect, and revalidates parent/path/fd/source identity after
connect before the first PRAGMA or `BEGIN`. Transaction reconciliation uses
the production connection-scoped store operation, with identity checks around
commit. The hard-link attack now returns nonzero without creating memory tables
or source sidecars; the parent swap leaves no target or staging file in either
the replacement directory or moved physical directory.

Final diff review exposed one still-earlier staging window: if the newly
created entry was replaced before its first path snapshot, the replacement
inode became the apparent baseline and an unrelated hard-linked SQLite victim
could be modified. A third RED reproduced the victim mutation. Staging
creation now records the empty regular file's full state directly from the
exclusive creation fd before close and requires the dirfd-relative path to
match it exactly. The victim remains byte/metadata-identical and the migration
fails safely.

Fresh local evidence after round 5 is migration `51 passed`, store `17 passed`,
combined focused regression `68 passed in 67.98s`, full verification `371
passed, 3 deselected in 69.73s`, and a clean quality gate across Ruff, Black,
mypy, and Bandit. `git diff --check` exits zero with no output.

## Review Fix Round 6: Install Binding and Recoverable Cleanup

The round-5 verifier reran every required gate and the earlier adversarial
checks, then returned NOT READY with two additional Important local-file
correctness findings. First, replacing the populated staging entry after its
SQLite transaction but immediately before installation caused the old code to
hard-link the source database into the destination and report
`status=created`. Second, moving an unrelated single-link SQLite file onto the
random staging name before its first state check made safe-failure cleanup
unlink that file's only recoverable path.

Both exact real-SQLite scenarios were added as regressions and first reported
two expected failures. New-destination commit now revalidates the populated
staging entry against its creation device/inode and source identity immediately
before installation. After the no-replace hard link, it repeats that check and
requires the installed destination to have the creation inode. A mismatch
enters rollback; rollback accepts either the known creation inode or the
just-linked current staging inode, removes only the link created by this
operation, and does not mutate the source or replacement database.

Staging cleanup is now identity-aware. It removes the main staging name only
when it still has the expected creation device/inode or is an additional safe
hard-link alias with another recoverable link. An unrelated replacement with a
single link is preserved at the random path and the command returns nonzero.
Unknown sidecar names are likewise preserved and reported rather than deleted
by pathname. The close/fstat path also closes the exclusive creation fd through
a `finally` block before any error cleanup.

The two new focused regressions are GREEN, and the combined install/cleanup,
pre-SQL hard-link, parent-swap, first-snapshot replacement, and close-failure
group reports `9 passed`. Fresh local evidence after round 6 is migration `53
passed`, store `17 passed`, combined focused regression `70 passed in 68.08s`,
full verification `373 passed, 3 deselected in 69.13s`, and a clean quality
gate across Ruff, Black, mypy, and Bandit. `git diff --check` exits zero with no
output.

## Final Independent Verification

A new read-only verifier reviewed the exact round-6 snapshot and returned
**READY**, with zero Critical, Important, or Minor code/report findings. Its
fresh results were migration `53 passed`, store `17 passed`, full verification
`373 passed, 3 deselected`, and clean Ruff, Black, mypy, and Bandit gates with
no warnings or issues. The focused local-file identity and consistency group
reported `9 passed`; marker coverage rejected all six required negative forms
and preserved the approved definition exactly.

Independent real-SQLite checks confirmed the final staging creation,
transaction, installation, rollback, and recoverable-cleanup identity
contracts. Repository status and SHA-256 hashes were identical before and
after verification, HEAD remained `65d3f0d103dc241c71b72d6e734a93d6794e123b`,
and the verifier modified no repository file. The approved README and
`docs/index.html` deferral remains assigned to Task 7.
