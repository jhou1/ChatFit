# Long-Term User Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit remember, update, and forget commands mutate a separate durable user-memory SQLite database and make every ChatFit Agent load those memories on every request.

**Architecture:** Add a focused `agents/memory/` package with a transactional SQLite repository and a dependency-injected command interpreter/service. Add `load_memories` and `memory_agent` nodes to the existing LangGraph; keep context governance as short-term compression. Production assembly passes a separate memory database path, while tests use real temporary SQLite files and deterministic interpreter decisions.

**Tech Stack:** Python 3.13, SQLite, Pydantic 2, LangGraph 1.2, LangChain structured output, FastAPI, Pytest, Ruff, Black, MyPy, Bandit

## Global Constraints

- Work only in `/Users/hjw/Projects/ChatFit/.worktrees/long-term-memory` on branch `codex/long-term-memory`.
- Store long-term memory in `user-memory.db`, never in the training database.
- A complete explicit remember command writes immediately without a second approval.
- Ambiguous or conflicting mutations do not change the database until clarified.
- Forget physically deletes the selected memory and aliases.
- Enforce canonical and alias uniqueness in SQLite, not only in prompts.
- Update retains the original memory ID and increments its version; it never inserts a second canonical memory.
- Preserve checkpoint and context-governance behavior as short-term context.
- Tests proving mutation query a real temporary SQLite database.
- Follow red-green-refactor for each behavior and avoid network LLM calls in tests.
- `make quality` and `make verify` must finish with no errors or warnings.
- Before completion, dispatch the independent verification subagent required by `AGENTS.md` and fix every reported finding.

## File Map

**Create:**

- `agents/memory/__init__.py` — public exports.
- `agents/memory/models.py` — records, decisions, results, and pending actions.
- `agents/memory/store.py` — schema, normalization, transactions, uniqueness, CRUD, and aliases.
- `agents/memory/agent.py` — interpreter protocol, LLM interpreter, and mutation service.
- `tests/test_user_memory_store.py` — real-SQLite repository tests.
- `tests/test_memory_agent.py` — explicit Chinese remember/update/forget tests.
- `tests/test_memory_graph.py` — routing, loading, prompt injection, thread, and isolation tests.
- `scripts/migrate_explicit_memories.py` — dry-run/apply legacy migration.
- `tests/test_memory_migration.py` — migration and source-integrity tests.

**Modify:**

- `agents/models.py`, all files under `agents/roles/`, `api.py`, `main.py`.
- `evaluation/models.py`, `evaluation/runner.py`, the golden JSONL and generator.
- `tests/test_api.py`, `tests/test_context_governance.py`, `tests/test_evaluation.py`.
- `.env.example`, `.gitignore`, `docker-compose.yml`, `README.md`, `docs/architecture.md`, and `docs/index.html` only if its copy is stale.

---

### Task 1: Define memory models and initialize the isolated database

**Files:**
- Create: `agents/memory/__init__.py`
- Create: `agents/memory/models.py`
- Create: `agents/memory/store.py`
- Create: `tests/test_user_memory_store.py`

**Produces:** `MemoryType`, `UserMemory`, `NewUserMemory`, `MemoryUpdate`, `RememberResult`, `MemoryConflictError`, `StaleMemoryError`, `owner_key_for`, `normalize_memory_key`, and `UserMemoryStore`.

- [ ] **Step 1: Write failing schema, owner, and normalization tests**

Create `tests/test_user_memory_store.py`:

```python
import sqlite3

from agents.memory.models import MemoryType
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for


def test_initializes_only_user_memory_tables(tmp_path):
    db_path = tmp_path / "user-memory.db"
    UserMemoryStore(db_path)
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"user_memories", "user_memory_aliases"} <= tables
    assert "training_sessions" not in tables


def test_owner_and_alias_normalization_are_stable():
    owner = owner_key_for("telegram-123")
    assert owner == owner_key_for("telegram-123")
    assert len(owner) == 64
    assert "telegram-123" not in owner
    assert normalize_memory_key(" ２‐１‐３ ") == "2-1-3"
    assert MemoryType.TRAINING_TEMPLATE.value == "training_template"
```

- [ ] **Step 2: Run RED**

```bash
UV_PROJECT_ENVIRONMENT=/Users/hjw/Projects/ChatFit/.venv UV_CACHE_DIR=/tmp/chatfit_memory_uv_cache uv run pytest tests/test_user_memory_store.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'agents.memory'`.

- [ ] **Step 3: Implement typed models**

In `agents/memory/models.py`, use `StrEnum` for the six approved memory types and frozen Pydantic models for stored records. Include these exact fields:

```python
class UserMemory(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    owner_key: str
    memory_type: MemoryType
    canonical_key: str
    display_name: str
    content: str
    aliases: tuple[str, ...] = ()
    version: int = Field(ge=1)
    created_at: str
    updated_at: str
```

`NewUserMemory` contains type, key, display name, content, and aliases. `MemoryUpdate` contains display name, content, aliases, and `expected_version`. `RememberResult` contains status and memory. Define focused conflict and stale-version exceptions.

- [ ] **Step 4: Implement normalization and the approved two-table schema**

`owner_key_for` returns SHA-256 of `chatfit-user-memory:{user_id}`. `normalize_memory_key` applies NFKC, lowercase Latin, whitespace collapse, Unicode hyphen/colon normalization, and removes whitespace around those separators.

`UserMemoryStore.__init__(db_path)` creates the parent, rejects an existing directory-shaped target, enables foreign keys, and creates exactly `user_memories` and `user_memory_aliases` with:

```sql
UNIQUE (owner_key, memory_type, canonical_key)
PRIMARY KEY (owner_key, normalized_alias)
FOREIGN KEY (memory_id, owner_key)
    REFERENCES user_memories (id, owner_key) ON DELETE CASCADE
```

Do not call the business database initializer.

- [ ] **Step 5: Run GREEN and commit**

Run the Task 1 test command; expect all tests to pass without warnings.

```bash
git add agents/memory tests/test_user_memory_store.py
git commit -m "feat: add isolated user memory store"
```

---

### Task 2: Enforce uniqueness, idempotency, stable update, and physical delete

**Files:**
- Modify: `agents/memory/models.py`
- Modify: `agents/memory/store.py`
- Modify: `tests/test_user_memory_store.py`

**Produces:** `list_memories(owner_key)`, `resolve(owner_key, query)`, `remember(owner_key, memory)`, `update(owner_key, memory_id, change)`, and `forget(owner_key, memory_id)`.

- [ ] **Step 1: Write failing remember and alias tests**

Create `training_template:213` with aliases `213`, `2-1-3`, and `壶铃213`. Query the DB and assert one row. Call remember twice and assert:

```python
assert first.status == "created"
assert second.status == "unchanged"
assert first.memory.id == second.memory.id
assert memory_row_count == 1
assert {store.resolve(owner, alias)[0].id for alias in aliases} == {first.memory.id}
```

Add a conflicting-content test expecting `MemoryConflictError` and unchanged content/version.
Add a second owner and prove list, resolve, update, and forget cannot observe or
mutate the first owner's row.

- [ ] **Step 2: Run RED**

Run the store file with `-k 'remember or alias or conflict'`. Expected: missing CRUD methods.

- [ ] **Step 3: Implement transactional remember and resolve**

Use one fresh connection per operation, foreign keys, a bounded busy timeout, and `BEGIN IMMEDIATE`. Normalize and deduplicate all keys. Return unchanged for the same canonical content, raise before mutation for conflicts, otherwise insert one UUID row and aliases. Never use `INSERT OR REPLACE`. Convert concurrent integrity errors into a re-read: identical content is unchanged; different content is a conflict.

- [ ] **Step 4: Write failing stable update, stale version, and delete tests**

Resolve through `壶铃213`, update, and query both tables:

```python
assert updated.id == original.id
assert updated.version == original.version + 1
assert updated.content == replacement
assert memory_row_count == 1
```

Assert a stale expected version raises `StaleMemoryError` without changing data. Forget the exact row and assert both memory and alias counts are zero.

- [ ] **Step 5: Run RED, then implement update/delete**

Run with `-k 'update or stale or forget'`; expect failures. Implement an owner-, ID-, and version-scoped SQL `UPDATE`, replace aliases inside the same transaction, and roll back on conflict. Do not call `remember` from `update`. Implement owner-scoped `DELETE` and rely on cascade for aliases.

- [ ] **Step 6: Prove concurrent uniqueness**

Use `ThreadPoolExecutor(max_workers=2)` plus a barrier to remember identical `training_template:213` values concurrently. Accept one created and one unchanged result; query exactly one canonical row and one memory ID. Run the entire store file until it passes without lock warnings.

- [ ] **Step 7: Commit**

```bash
git add agents/memory tests/test_user_memory_store.py
git commit -m "feat: enforce unique durable memories"
```

---

### Task 3: Implement explicit remember, update, clarification, and forget behavior

**Files:**
- Modify: `agents/memory/models.py`
- Create: `agents/memory/agent.py`
- Modify: `agents/memory/__init__.py`
- Create: `tests/test_memory_agent.py`

**Produces:** `MemoryInterpreter` protocol, `LLMMemoryInterpreter`, `MemoryAgent`, `MemoryMutationDecision`, `PendingMemoryAction`, and `MemoryAgentResult`.

- [ ] **Step 1: Read the test-quality reference**

Read `/Users/hjw/.codex/plugins/cache/openai-curated-remote/superpowers/6.2.0/skills/test-driven-development/writing-good-tests.md` completely before writing tests.

- [ ] **Step 2: Write the failing explicit remember test**

Use a deterministic interpreter decision but the real store:

```python
result = await agent.handle(
    user_id="user-a",
    user_message="记住我乳糖不耐受",
    pending=None,
)
with sqlite3.connect(memory_db) as connection:
    row = connection.execute(
        "SELECT memory_type, canonical_key, content FROM user_memories"
    ).fetchone()
assert row == ("dietary_preference", "乳糖不耐受", "我乳糖不耐受")
assert "已记住" in result.response
assert result.pending is None
```

- [ ] **Step 3: Run RED**

Run the single remember test. Expected: missing `MemoryAgent`/interpreter classes.

- [ ] **Step 4: Implement decisions, interpreter protocol, and immediate remember**

Use four intents: remember, update, forget, clarify. The structured decision carries type, key, display name, exact content, aliases, target query, and clarification question. `MemoryAgent.handle` derives the owner, invokes the interpreter, executes the store, and returns deterministic Chinese text only after commit.

Add `extract_explicit_memory_payload`: prefix `记住<content>` stores the exact suffix; suffix `<content>，记下来` stores the exact prefix. The model may classify/name the memory but cannot replace the explicit source payload silently.

- [ ] **Step 5: Run GREEN**

Run the single remember test and confirm one committed row.

- [ ] **Step 6: Write failing update, duplicate, forget, and clarification sequences**

Test these database-backed flows:

1. Remember `training_template:213`.
2. Send `把壶铃213更新成新的模板内容`.
3. Assert same row ID, version 2, replacement content, total count 1.
4. Repeat the same remember command and assert one row.
5. Send `忘掉我乳糖不耐受` and assert the row/aliases are absent.
6. Send `忘掉那个训练模板`, assert a clarification response and unchanged counts.
7. Confirm one pending target and assert only that row is deleted.
8. Force the store to raise and assert the response does not contain `已记住`,
   `已更新`, or `已忘掉`.

- [ ] **Step 7: Run RED, then implement mutation and pending flows**

Run the full file; expect update/delete/clarification failures. Resolve through aliases. Execute only one exact target. Keep a `PendingMemoryAction` without mutation for zero/multiple/incomplete targets. Confirmation uses the captured version. A stale version asks the user to review the newer value. Repository exceptions never produce success text.

- [ ] **Step 8: Implement and test structured LLM interpretation**

Use `create_chat_model(llm_config).with_structured_output(MemoryMutationDecision)`. Prompt it to preserve explicit content, inspect current memory names/aliases, and clarify rather than guess. Test with a fake structured-output runnable and assert exact input messages plus validated output; do not call the network.

- [ ] **Step 9: Run GREEN and commit**

Run store and memory-agent files; expect all pass without warnings.

```bash
git add agents/memory tests/test_memory_agent.py tests/test_user_memory_store.py
git commit -m "feat: handle explicit memory commands"
```

---

### Task 4: Load durable memory into the graph and every Agent prompt

**Files:**
- Modify: `agents/models.py`
- Modify: `agents/roles/supervisor.py`
- Modify: `agents/roles/training.py`
- Modify: `agents/roles/meal.py`
- Modify: `agents/roles/insights.py`
- Create: `tests/test_memory_graph.py`
- Modify: `tests/test_context_governance.py`

**Changes:** `make_agent_graph` gains keyword-only `memory_store` and `memory_interpreter` dependencies. `AgentState` gains `memory_context` and nullable `pending_memory_action`.

- [ ] **Step 1: Write failing fresh-load, new-thread, and isolation tests**

Build a graph with a real temp store and deterministic interpreter. Patch existing LLM/routing boundaries. Remember in thread A, invoke thread B with the same `configurable.user_id`, and assert B contains the saved context. Invoke another user and assert it is absent.

- [ ] **Step 2: Run RED**

Run `tests/test_memory_graph.py -k 'load or thread or isolation' -v`. Expected: graph/state have no durable memory.

- [ ] **Step 3: Add `load_memories` and `memory_agent` nodes**

Run `load_memories` before context governance and replace the state context from a fresh DB read. Use `configurable.user_id`, falling back to `thread_id` for old direct callers. Format a `[Durable User Memories — database-backed]` block. Add `memory_agent` conditional routing and refresh context after successful mutation.

Rename the context-governance prompt role to “conversation context summarizer”; retain its summary field and message truncation unchanged.

- [ ] **Step 4: Write failing explicit routing tests**

Assert `记住我乳糖不耐受`, `我不吃香菜，记下来`, `把 2-1-3 模板更新成新的内容`, and `忘掉乳糖不耐受` include `memory_agent`; ordinary `今天练了深蹲` does not. A pending-action confirmation routes back to memory.
Add a composite `记住这个模板并分析今天的训练` case and assert memory plus
the LLM-selected Insights Agent are both retained.

- [ ] **Step 5: Implement routing guard**

Add focused remember/forget/update phrases around memory, preference, and template language. Preserve the LLM router for other/composite routing, prepend memory once, and remove fallback chatter beside a concrete agent.

- [ ] **Step 6: Write failing prompt-injection tests**

Patch each role’s safe LLM boundary and assert Supervisor, Chatter, Training, Meal, and Insights system inputs contain the durable block. Assert the short-term summary has a distinct label.

- [ ] **Step 7: Inject one shared context formatter**

Add one helper in the memory package to append durable memory and conversation summary. Use it in all role prompts; do not duplicate formatting strings.

- [ ] **Step 8: Run GREEN and commit**

Run memory graph/context tests including e2e-marked context tests, then relevant role tests. Expect all pass.

```bash
git add agents tests/test_memory_graph.py tests/test_context_governance.py
git commit -m "feat: load memory into every agent"
```

---

### Task 5: Assemble persistence in API, CLI, and evaluation

**Files:**
- Modify: `api.py`, `main.py`, `evaluation/runner.py`, `tests/test_api.py`
- Modify: `.env.example`, `.gitignore`, `docker-compose.yml`

**Produces:** `get_user_memory_db_path()` and production graph config containing `thread_id` plus `user_id`.

- [ ] **Step 1: Write failing path, identity, and `/clear` tests**

Mirror checkpointer path tests. Assert parent creation, reject a directory-shaped path, pass exact request `user_id` to graph config, and prove `/clear` changes only `user_sessions` without deleting memory.
Build two application lifespans against the same memory path and prove the
second graph loads a row written during the first lifespan.

- [ ] **Step 2: Run RED**

Run `tests/test_api.py -k 'memory or configurable_user or clear' -v`. Expected: no memory path/dependency/identity behavior.

- [ ] **Step 3: Initialize production dependencies**

Default `USER_MEMORY_DB_PATH` to `user-memory.db`. API lifespan creates the store and LLM interpreter and passes both to the graph. Add `user_id` to configurable state. Do not change the business or checkpointer DB paths.

Main initializes the configured store and uses owner `local-cli`. Evaluation creates one memory DB per case temp directory and uses `case.case_id` as user ID so turns share memory and cases remain isolated.

- [ ] **Step 4: Configure runtime persistence**

Add `USER_MEMORY_DB_PATH=/app/data/user-memory.db` to Compose and `.env.example`. Explicitly ignore `user-memory.db`, `user-memory.db-wal`, and `user-memory.db-shm`, retaining the broad runtime-data ignore.

- [ ] **Step 5: Run GREEN and commit**

Run API/memory graph tests and `make verify`; expect all selected tests pass and `/clear` semantics remain unchanged.

```bash
git add api.py main.py evaluation/runner.py tests/test_api.py .env.example docker-compose.yml .gitignore
git commit -m "feat: persist user memory across restarts"
```

---

### Task 6: Migrate the explicit legacy `2-1-3` template

**Files:**
- Create: `scripts/migrate_explicit_memories.py`
- Create: `tests/test_memory_migration.py`

**Produces:** read-only candidate scan and a CLI accepting `--source-db`, `--memory-db`, `--user-id`, and `--apply`.

- [ ] **Step 1: Write failing dry-run/apply/idempotency tests**

Create a source fixture containing the complete historical definition: `2-1-3
是一个训练模板（你需要记忆一下），它代表2个抓举，1个挺举，3个长循环。
第一分钟左手一次，第二分钟右手一次，第三分钟双手一次，然后是10个波比跳
和左右手各两次 thruster。` Include an ordinary note. Assert dry-run writes
nothing, apply creates one `training_template:213`, a second apply still leaves
one row, and source schema/rows are unchanged.

- [ ] **Step 2: Run RED**

Run `tests/test_memory_migration.py -v`. Expected: missing migration module.

- [ ] **Step 3: Implement read-only extraction**

Open the source with SQLite URI `mode=ro`. Select distinct notes containing `记住` or `需要记忆`. Use deterministic regex for `<key> 是一个训练模板……它代表<definition>`. Print recognized/unrecognized candidates; do not use an LLM.

- [ ] **Step 4: Implement idempotent apply**

On `--apply`, construct `NewUserMemory` type training template, canonical key `213`, complete definition, aliases `213`, `2-1-3`, `壶铃213`, and call the production store. Never write through the source connection. Invalid paths/conflicts return nonzero.

- [ ] **Step 5: Run GREEN and commit**

Run migration and store tests; expect source unchanged and one destination row after repeated apply.

```bash
git add scripts/migrate_explicit_memories.py tests/test_memory_migration.py
git commit -m "feat: migrate explicit legacy memories"
```

---

### Task 7: Add memory DB evaluation assertions and documentation

**Files:**
- Modify: `evaluation/models.py`, `evaluation/runner.py`, `evaluation/chatfit_golden_test_set.jsonl`
- Modify: `scripts/generate_golden_test_set.py`, `tests/test_evaluation.py`
- Modify: `README.md`, `docs/architecture.md`
- Inspect/modify if stale: `docs/index.html`

**Changes:** `ExpectedTrajectoryAssertion.database` accepts `business` or `memory`, defaulting to business.

- [ ] **Step 1: Write failing evaluation-contract tests**

Accept this assertion and reject unknown database names:

```json
{
  "eval_type": "db_state",
  "database": "memory",
  "query": "SELECT COUNT(*) FROM user_memories WHERE canonical_key='乳糖不耐受'",
  "expected_value": 1
}
```

Add a runner-helper test proving memory assertions query the memory path while old assertions query the business path.

- [ ] **Step 2: Run RED, then implement typed DB selection**

Run evaluation tests; expect schema/selection failures. Add `Literal["business", "memory"]` and a scalar-query helper choosing the correct path while preserving existing failure codes.

- [ ] **Step 3: Update checked-in/generated memory cases**

For `ME_02`, `ME_03`, and `ME_06`, replace obsolete context-governance save/delete trajectories with MemoryAgent and add memory DB assertions. Remember turns assert count 1; forget asserts count 0. Regenerate JSONL and prove deterministic output.

- [ ] **Step 4: Update documentation**

Document explicit behavior, clarification, uniqueness, separate storage, `/clear`/restart persistence, migration commands, and the distinction between checkpoint, summary, business data, and durable memory. Inspect `docs/index.html` and change only stale claims.

- [ ] **Step 5: Run GREEN and commit**

Run evaluation and all new memory tests, then `make verify`; expect no failures/warnings.

```bash
git add evaluation scripts/generate_golden_test_set.py tests/test_evaluation.py README.md docs/architecture.md docs/index.html
git commit -m "test: verify durable memory behavior"
```

---

### Task 8: Final quality gates and independent verification

**Files:** Verify all Task 1–7 changes; modify only files required by findings.

- [ ] **Step 1: Run static quality**

```bash
UV_PROJECT_ENVIRONMENT=/Users/hjw/Projects/ChatFit/.venv UV_CACHE_DIR=/tmp/chatfit_memory_uv_cache make quality
```

Expected: Ruff, Black, MyPy, Bandit pass without errors/warnings. Review the diff because quality includes formatting.

- [ ] **Step 2: Run full verification**

```bash
UV_PROJECT_ENVIRONMENT=/Users/hjw/Projects/ChatFit/.venv UV_CACHE_DIR=/tmp/chatfit_memory_uv_cache make verify
```

Expected: all selected tests pass without warnings.

- [ ] **Step 3: Run explicit acceptance sequence**

Execute deterministic Agent tests sending `记住`, alias update via `壶铃213`, a new thread load, and `忘掉`. Inspect SQLite: one stable updated template row before forget, zero selected rows after forget.

- [ ] **Step 4: Dispatch required verifier subagent**

The verifier reads `docs/quality.md`, approved spec/plan, and full diff; checks isolation and uniqueness; runs both gates; and reports every error, failure, warning, and stale document.

- [ ] **Step 5: Fix findings with regression tests and repeat**

For behavioral findings, create a failing regression first, apply the smallest fix, rerun both gates, and dispatch another independent pass. Repeat until clean.

- [ ] **Step 6: Confirm branch cleanliness**

```bash
git status --short
git diff main...HEAD --check
git log --oneline --decorate main..HEAD
```

Expected: no uncommitted changes, no whitespace errors, and focused commits.
