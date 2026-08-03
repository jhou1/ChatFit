# Long-Term User Memory Design

**Date:** 2026-08-03  
**Status:** Approved for implementation planning  
**Scope:** Explicit, durable, user-scoped memories for ChatFit

## Problem

ChatFit currently has two forms of persisted context, neither of which fulfills
an explicit user request such as “记住训练模板 2-1-3”:

1. LangGraph checkpoints preserve messages within a `thread_id`.
2. Context governance compresses older messages into a thread-local summary.

Training and meal records are durable business data, but a sentence stored in a
training note is not a long-term memory. The only available training-history
tool returns joined per-set rows for a date window, and its output can be
truncated before an older definition is reached. A user can therefore explicitly
ask ChatFit to remember something, have the words appear in a training note, and
still receive a later response that ChatFit does not know it.

The required contract is stronger: an explicit “remember” command must update a
dedicated durable store, every later Agent run must be able to use that store,
and an explicit “forget” command must remove the matching durable memory.

## Historical Design Reconciliation

Repository history contains an earlier `UserProfile` model with diet and
training preferences plus a stub `memory_store.py`. That stub explicitly called
for persistent, cross-thread, long-term memory but never implemented storage.
Those files were later removed. A subsequent context-governance implementation
introduced the current thread-summary mechanism.

The implementation will merge the intent of those designs without restoring the
obsolete `agent/` package:

- context governance remains short-term conversation compression;
- the old `UserProfile` idea becomes categories in a general memory model;
- the unimplemented long-term store becomes a focused `agents/memory/` package;
- no second graph, state model, or parallel memory abstraction is introduced.

## Goals

- Persist explicit remember commands immediately when they are complete and
  unambiguous.
- Load current memories on every conversation request, independent of thread or
  process lifetime.
- Support exact, transactional update and physical deletion.
- Ask for clarification before any ambiguous or conflicting mutation.
- Enforce one canonical memory per user, memory type, and canonical key at the
  database layer.
- Make repeated saves idempotent and make alias-based updates target the
  original row.
- Keep user memory physically and logically separate from training and meal
  business data.
- Preserve user isolation.
- Recover the existing explicitly marked `2-1-3` template through a safe,
  repeatable migration.
- Prove mutations by querying a real temporary memory database in tests.

## Non-Goals

- Replacing LangGraph checkpoints.
- Moving training or meal data out of its existing database.
- Storing ordinary conversation automatically as long-term memory.
- Inferring sensitive facts that the user did not explicitly ask ChatFit to
  remember.
- Adding vector search in the first implementation.
- Keeping deleted memory content in an audit or soft-delete table.

## Storage Boundary

Long-term memory uses a separate SQLite database file. It does not add tables to
the training database.

```dotenv
USER_MEMORY_DB_PATH=/app/data/user-memory.db
```

The Compose deployment already persists `/app/data` through `runtime-data`, so
the default container path survives restarts. Local execution defaults to
`user-memory.db` unless configured otherwise. The filename and its WAL/SHM
companions are ignored by Git.

The API derives a stable `owner_key` as the SHA-256 digest of a domain-separated
user identifier. The raw Telegram/API user identifier is not stored in the
memory database. The same input identifier always produces the same owner key,
so `/clear` and process restarts do not change memory ownership.

## Data Model

The store uses normalized canonical keys and aliases. For example, the display
name `2-1-3` has canonical key `213`; the surface forms `213`, `2-1-3`, and
`壶铃213` are aliases for the same row.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_memories (
    id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN (
            'training_template',
            'training_preference',
            'dietary_preference',
            'health_constraint',
            'profile',
            'other'
        )
    ),
    canonical_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_key, memory_type, canonical_key),
    UNIQUE (id, owner_key)
);

CREATE TABLE IF NOT EXISTS user_memory_aliases (
    owner_key TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    display_alias TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    PRIMARY KEY (owner_key, normalized_alias),
    FOREIGN KEY (memory_id, owner_key)
        REFERENCES user_memories (id, owner_key)
        ON DELETE CASCADE
);
```

The main unique constraint guarantees that a user cannot have two
`training_template:213` rows. The alias primary key guarantees that one alias
cannot silently point to two memories for the same user. SQLite foreign keys and
transactions preserve owner isolation and prevent orphan aliases.

Canonical keys and aliases use one deterministic normalization function:

- Unicode NFKC normalization;
- trimmed leading and trailing whitespace;
- lowercase Latin characters;
- collapsed internal whitespace;
- normalization of Unicode hyphen and colon variants to their ASCII forms;
- removal of whitespace around hyphens and colons.

The display name and display aliases preserve user-facing spelling.

## Module Boundaries

### `agents/memory/models.py`

Defines the memory types and structured Agent decisions:

- `UserMemory`
- `MemoryType`
- `MemoryMutationIntent`
- `MemoryMutationDecision`
- `PendingMemoryAction`

### `agents/memory/store.py`

Owns schema initialization and all SQL. Its public operations are:

```python
def owner_key_for(user_id: str) -> str: ...
def normalize_memory_key(value: str) -> str: ...

class UserMemoryStore:
    def list_memories(self, owner_key: str) -> list[UserMemory]: ...
    def resolve(self, owner_key: str, query: str) -> list[UserMemory]: ...
    def remember(self, owner_key: str, memory: NewUserMemory) -> RememberResult: ...
    def update(self, owner_key: str, memory_id: str, change: MemoryUpdate) -> UserMemory: ...
    def forget(self, owner_key: str, memory_id: str) -> bool: ...
```

`remember` is idempotent. The same canonical key and same content returns the
existing row. It never overwrites conflicting content. An explicit update calls
`update` against the resolved `memory_id`; it does not implement update as
delete-plus-insert or as an unconstrained upsert. The row ID remains stable and
`version` increments.

All changes to a memory and its aliases occur in one transaction. Integrity
errors are converted to domain conflicts and never leak raw SQL errors to the
Agent.

### `agents/memory/agent.py`

Interprets explicit memory commands and executes store operations. It uses a
structured `MemoryMutationDecision` rather than parsing unconstrained prose.
User content is preserved as the stored source of truth; the model supplies the
type, canonical subject, aliases, and whether clarification is required.

### Existing graph

The existing graph receives two focused additions:

1. `load_memories` runs at the beginning of every request and replaces the
   in-state memory snapshot with a fresh database read.
2. `memory_agent` handles explicit remember, update, and forget commands.

The routing prompt includes `memory_agent`, and a deterministic guard adds it to
the selected agents whenever the latest user message contains an explicit
memory mutation phrase such as `记住`, `记下来`, `更新这个记忆`, `改成`, `忘掉`, or
`删除这条记忆`. The other relevant agents may still run for a composite request.

`AgentState` gains a memory snapshot and an optional pending action. The snapshot
is refreshed on every request rather than trusted as durable state. A pending
action exists only to carry a clarification through the current conversation;
the database remains the source of truth.

The API passes `user_id` through LangGraph configurable state. Direct graph
callers that omit it use their `thread_id` as an isolated fallback owner for
backward-compatible tests and evaluation. The local CLI uses the fixed owner
identifier `local-cli`.

## Memory Context Injection

Every Agent receives a clearly delimited memory block built from the fresh
snapshot:

```text
[Durable User Memories — database-backed]
- training_template / 2-1-3: ...
- dietary_preference / lactose: ...
```

The block is added to Supervisor routing and to Training, Meal, Insights, and
Chatter system prompts. Agent instructions state that this block is durable user
data, while the existing historical summary is fallible short-term context. The
implementation initially loads all active memories because this is a personal
assistant with a small explicit store. Vector retrieval can be added behind the
same store interface if the data set later becomes too large.

## Mutation Behavior

### Remember

For a complete, unambiguous command such as `记住我乳糖不耐受`, the Memory Agent
writes immediately. The explicit command is the authorization; no second
approval is requested. It confirms success only after the transaction commits.

If the same canonical memory and content already exist, the operation succeeds
idempotently without changing the row or version. If the canonical target or an
alias already identifies different content, the Agent asks whether the user
wants to update it and does not write yet.

### Update

For an explicit update such as `把 2-1-3 更新成……`, aliases resolve the existing
row. A single exact match is updated transactionally. Its `id` and `created_at`
remain unchanged; `content`, aliases, `updated_at`, and `version` change.

Zero matches, multiple semantic candidates, or an incomplete replacement cause
a clarification question with no database mutation. Confirmation after
clarification executes the pending update against the same resolved row and
rechecks its version so a concurrent change cannot be overwritten silently.

### Forget

For an exact command such as `忘掉我乳糖不耐受`, the matching memory and its
aliases are physically deleted in one transaction. Multiple candidates or an
unclear reference such as `忘掉那个训练模板` causes clarification and leaves the
database unchanged. A missing target produces a factual “not found” response
and never creates a replacement row.

### Failure handling

- Extraction uncertainty leads to clarification.
- Alias or canonical-key conflict leads to clarification.
- A database exception rolls back the transaction and returns a save/update/
  delete failure; the Agent must not claim success.
- Loading failure is fail-closed for memory mutation and fail-open for unrelated
  chat: unrelated Agents may answer without a memory block, but an explicit
  memory command reports that durable memory is unavailable.
- Observability records operation type, result, and opaque owner/memory IDs but
  not memory content.

## Existing `2-1-3` Migration

A one-time script, `scripts/migrate_explicit_memories.py`, reads but never
modifies the training database. It accepts explicit source, destination, and
user options and defaults to dry-run:

```bash
python scripts/migrate_explicit_memories.py \
  --source-db ~/.iron/iron.db \
  --memory-db runtime-data/user-memory.db \
  --user-id <telegram-user-id>
```

Only notes with explicit remember markers such as `记住` or `需要记忆` become
candidates. The first supported deterministic extractor recognizes the existing
`2-1-3 是一个训练模板……它代表……` form, derives canonical key `213`, and creates
the aliases `213`, `2-1-3`, and `壶铃213`. Unrecognized candidates are reported
but not imported. `--apply` performs the idempotent transaction. Repeated runs
leave one row because the store uniqueness contract applies to migration too.

The migration opens the source in SQLite read-only mode. Its test uses copied
fixture data in temporary files and verifies that the source schema and row data
are logically unchanged.

## Testing Strategy

All mutation tests use a real temporary SQLite database. A success message alone
is never accepted as proof.

### Store tests

- initialize the schema in a new file;
- isolate two users;
- remember and list dietary preferences and training templates;
- save the same memory twice and assert one row;
- attempt two concurrent inserts for `training_template:213` and assert one row;
- resolve `213`, `2-1-3`, and `壶铃213` to the same memory ID;
- update through an alias and assert stable ID, incremented version, and changed
  content;
- reject a conflicting save without changing the original row;
- forget and assert that both memory and aliases are physically absent;
- force an update failure and assert transaction rollback.

### Agent behavior tests

- send `记住我乳糖不耐受` and query the database for the dietary preference;
- send a complete `记住训练模板 2-1-3：……` command and query its content and
  aliases;
- create a new thread and prove the template is present in loaded memory context;
- send `把 2-1-3 更新成……` and prove the same row ID was updated rather than a
  second row inserted;
- send `忘掉我乳糖不耐受` and prove the row no longer exists;
- send `忘掉那个训练模板`, assert that the Agent asks for clarification, and
  prove the database is unchanged before confirmation;
- confirm one target and prove only that target is deleted;
- assert that a user cannot read, update, or forget another user's memory;
- assert that store failure cannot produce an “already remembered” response.

The memory interpreter is dependency-injected so tests can use deterministic
structured decisions without network calls while exercising the real graph node
and real SQLite store.

### API and regression tests

- `/clear` changes short-term thread context but preserves long-term memory;
- rebuilding the API application against the same memory file reloads memories;
- existing training, meal, checkpoint, approval, and proactive-review tests
  continue to pass;
- evaluation cases `ME_02`, `ME_03`, and `ME_06` gain database-backed assertions
  instead of relying only on response semantics;
- `make quality` and `make verify` pass without warnings.

## Documentation Changes

- Update `README.md` with the memory contract, configuration, storage boundary,
  and migration command.
- Update `docs/architecture.md` to distinguish short-term checkpoints from the
  independent long-term user-memory store.
- Update `docs/index.html` only if its product description claims behavior that
  would otherwise become stale.
- Rename prompt wording that calls context governance an “assistant memory
  manager” so documentation and implementation no longer conflate summaries
  with durable memories.

## Acceptance Criteria

The feature is complete only when all of the following are true:

1. A clear remember command commits to `user-memory.db` before success is
   reported.
2. A later request in a new thread receives the saved memory.
3. An alias-based update keeps the original row ID and never inserts a duplicate.
4. Database uniqueness prevents duplicate canonical keys and ambiguous alias
   ownership, including under concurrent writes.
5. A clear forget command physically removes the selected memory and aliases.
6. Ambiguous mutations ask for clarification and leave the database unchanged.
7. User A cannot observe or mutate user B's memories.
8. Existing explicitly marked `2-1-3` data can be migrated idempotently without
   changing the training database.
9. The full test and static-quality gates pass with no errors or warnings.
