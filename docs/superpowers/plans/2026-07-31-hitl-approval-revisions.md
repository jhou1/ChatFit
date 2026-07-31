# HITL Approval Revisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Preserve information supplied with an approval reply, supersede the old write, request approval for the revised draft, and make the eventual training write idempotent.

**Architecture:** The API passes the complete reply into the LangGraph interrupt instead of reducing it to a boolean. An injected LLM-backed resolver inside SafeToolNode distinguishes pure approval, revision, and rejection; revisions return the raw message to the training agent without executing the pending write. Approved training calls receive a stable operation ID that SQLite records atomically with the business rows.

**Tech Stack:** Python 3.13, LangGraph interrupts, LangChain messages/tools, Pydantic v2, SQLite, pytest/pytest-asyncio.

## Global Constraints

- Any new, corrected, or removed business information invalidates the old approval.
- “保存，同时 RPE 7” executes zero writes and triggers a revised approval request.
- Only a later pure approval executes the revised write.
- The final training write is idempotent by interrupted tool-call ID.
- Existing pure approval, rejection, read-tool, and direct database-call behavior remains compatible.
- Raw user messages never appear in observability attributes.
- Run all work in /Users/hjw/Projects/ChatFit/.worktrees/fix-hitl-context.

## File Structure

- tools/safe_execution.py: approval resolution, revision handling, and operation-ID injection.
- agents/roles/training.py: resolver injection and revised-draft instructions.
- agents/roles/meal.py: resolver injection for its SafeToolNode.
- api.py: raw interrupt resume replies.
- agents/models.py: optional system-owned operation_id.
- agents/sqlite_handler.py: atomic operation ledger.
- tests/test_safe_execution.py: resolver and state-transition coverage.
- tests/test_api.py: raw resume transport coverage.
- tests/test_sqlite_handler.py: idempotency coverage.
- README.md and docs/index.html: public behavior documentation.

---

### Task 1: Resolve Raw Approval Replies Inside SafeToolNode

**Files:**
- Modify: tools/safe_execution.py
- Modify: agents/roles/training.py
- Modify: agents/roles/meal.py
- Test: tests/test_safe_execution.py

**Interfaces:**
- Produces: ApprovalDecision(intent: Literal["approve", "revise", "reject"], feedback: str).
- Produces: ApprovalResolver.resolve(user_message: str, pending_tool_calls: list[dict]) -> ApprovalDecision.
- Changes: SafeToolNode(tools, approval_resolver=None).
- Consumes: interrupt resume data shaped as
  `{"user_message": "保存，同时 RPE 7"}`.

- [ ] **Step 1: Write the failing revision test**

Add a fake resolver and this behavior test to tests/test_safe_execution.py:

~~~python
class FakeApprovalResolver:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def resolve(self, user_message, pending_tool_calls):
        self.calls.append((user_message, pending_tool_calls))
        return self.decision


@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
async def test_revision_supersedes_write_and_preserves_reply(
    mock_execute, mock_interrupt
):
    resolver = FakeApprovalResolver(
        ApprovalDecision(intent="revise", feedback="保存，同时 RPE 7")
    )
    node = SafeToolNode(tools=[], approval_resolver=resolver)
    pending = {
        "name": "log_training_session",
        "args": {"sessions": [{"rpe": None}]},
        "id": "training-1",
    }
    mock_interrupt.return_value = {"user_message": "保存，同时 RPE 7"}

    result = await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    mock_execute.assert_not_called()
    assert resolver.calls == [("保存，同时 RPE 7", [pending])]
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["messages"][0].status == "error"
    assert "superseded" in result["messages"][0].content
    assert isinstance(result["messages"][1], HumanMessage)
    assert result["messages"][1].content == "保存，同时 RPE 7"
~~~

- [ ] **Step 2: Write the failing resolver parsing test**

Mock _execute_llm_query_safely to return {"intent":"revise"}, call ApprovalResolver.resolve with “保存，同时 RPE 7”, and assert it returns ApprovalDecision(intent="revise", feedback="保存，同时 RPE 7"). Also add malformed-JSON coverage and assert safe rejection.

- [ ] **Step 3: Run tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_safe_execution.py -q
~~~

Expected: collection or assertion failure because ApprovalDecision, ApprovalResolver, constructor injection, and revision behavior do not exist.

- [ ] **Step 4: Implement the typed resolver**

In tools/safe_execution.py, add:

~~~python
class ApprovalDecision(BaseModel):
    intent: Literal["approve", "revise", "reject"]
    feedback: str


class ApprovalResolverProtocol(Protocol):
    async def resolve(
        self, user_message: str, pending_tool_calls: list[dict]
    ) -> ApprovalDecision:
        pass


class ApprovalIntentModel(BaseModel):
    intent: Literal["approve", "revise", "reject"]


class ApprovalResolver:
    def __init__(self, llm_config: LLMConfig):
        self.llm = create_chat_model(llm_config)

    async def resolve(
        self, user_message: str, pending_tool_calls: list[dict]
    ) -> ApprovalDecision:
        instruction = (
            "Classify a reply to a pending database-write approval. Return "
            "approve only when it purely approves the exact pending data. "
            "Return revise when it adds, corrects, removes, or replaces any "
            "business data, even if it also says approve. Return reject when "
            "it declines the write. Output only JSON with one intent field."
        )
        context = json.dumps(pending_tool_calls, ensure_ascii=False, default=str)
        resolver_messages = [
            SystemMessage(content=instruction),
            HumanMessage(
                content=f"Pending tool calls: {context}\nUser reply: {user_message}"
            ),
        ]
        response = await _execute_llm_query_safely(self.llm, resolver_messages)
        try:
            payload = json.loads(extract_text(response["messages"]))
            intent = ApprovalIntentModel.model_validate(payload).intent
        except (ValueError, TypeError, ValidationError):
            intent = "reject"
        return ApprovalDecision(intent=intent, feedback=user_message)
~~~

Define ApprovalIntentModel as a Pydantic model containing only the literal intent. The resolver prompt includes pending tool calls and the complete reply but the observability layer receives only existing content-length/hash-safe attributes.

- [ ] **Step 5: Implement revision behavior**

After interrupt returns, resolve the raw user message. For revise:

1. execute any parallel read calls using the existing safe executor;
2. produce an error ToolMessage containing “Pending write superseded by user revision” for every write call;
3. preserve original tool-call ordering;
4. append HumanMessage(content=decision.feedback);
5. return without executing a write.

For reject, retain existing behavior. For approve, continue to execution.

- [ ] **Step 6: Inject the resolver**

Construct the training node with:

~~~python
SafeToolNode(
    tools=[
        normalize_practice_name,
        log_training_session,
        retrieve_training_sessions,
    ],
    approval_resolver=ApprovalResolver(llm_config),
)
~~~

Construct the meal node with:

~~~python
SafeToolNode(
    tools=[log_meal, advise_meals],
    approval_resolver=ApprovalResolver(llm_config),
)
~~~

- [ ] **Step 7: Run tests and verify GREEN**

Run uv run pytest tests/test_safe_execution.py -q. Expected: all tests pass without warnings.

- [ ] **Step 8: Commit**

~~~bash
git add tools/safe_execution.py agents/roles/training.py agents/roles/meal.py tests/test_safe_execution.py
git commit -m "feat: resolve HITL revisions inside safe tool node"
~~~

---

### Task 2: Pass Complete Resume Messages Through the API

**Files:**
- Modify: api.py
- Test: tests/test_api.py

**Interfaces:**
- Produces: Command(resume={interrupt_id: {"user_message": req.message}}).
- Removes: API-level _classify_approval_intent.

- [ ] **Step 1: Write the failing API test**

Make FakeResumeAgent capture the action passed to astream. POST “保存，同时 RPE 7” while interrupt-123 is pending and assert:

~~~python
assert agent.action.resume == {
    "interrupt-123": {"user_message": "保存，同时 RPE 7"}
}
~~~

Update observability coverage to expect hitl.reply_received with interrupt count and IDs only.

- [ ] **Step 2: Run the test and verify RED**

Run uv run pytest tests/test_api.py::test_chat_passes_complete_revision_reply_to_pending_interrupt -q. Expected: FAIL because the API resumes with approved and feedback.

- [ ] **Step 3: Implement raw resume transport**

Delete _classify_approval_intent. Replace the pending branch with:

~~~python
emit_event(
    "hitl.reply_received",
    {
        "interrupt.count": len(interrupts),
        "interrupt.ids": [str(intr.id) for intr in interrupts],
    },
)
resume_data = {
    intr.id: {"user_message": req.message}
    for intr in interrupts
}
action_command = Command(resume=resume_data)
~~~

Do not emit req.message.

- [ ] **Step 4: Run tests and verify GREEN**

Run uv run pytest tests/test_api.py -q. Expected: all API tests pass without warnings.

- [ ] **Step 5: Commit**

~~~bash
git add api.py tests/test_api.py
git commit -m "fix: preserve complete HITL resume replies"
~~~

---

### Task 3: Make Approved Training Writes Idempotent

**Files:**
- Modify: agents/models.py
- Modify: agents/sqlite_handler.py
- Modify: tools/safe_execution.py
- Test: tests/test_sqlite_handler.py
- Test: tests/test_safe_execution.py

**Interfaces:**
- Adds: TrainingInputRecorder.operation_id: str | None = None.
- Adds: operation_id="hitl:<tool_call_id>" to approved log_training_session calls.
- Adds: write_operations(operation_id, tool_name, result, created_at).

- [ ] **Step 1: Write failing database tests**

Create a TrainingInputRecorder with operation_id="hitl:training-1", one weighted session, and one set. Call add_training_session twice and assert both return “Training log saved successfully!”, training_sessions count is 1, training_sets count is 1, and write_operations count is 1. Add a second test with no operation ID and assert two direct calls still create two sessions.

- [ ] **Step 2: Write the failing injection test**

Resolve a pure approval and capture the call passed to _execute_single_tool_safely:

~~~python
assert executed["args"]["operation_id"] == "hitl:training-1"
assert "operation_id" not in pending["args"]
~~~

The second assertion proves checkpoint-owned calls are not mutated.

- [ ] **Step 3: Run tests and verify RED**

Run uv run pytest tests/test_sqlite_handler.py tests/test_safe_execution.py -q. Expected: unknown or absent operation_id behavior and duplicate rows.

- [ ] **Step 4: Add the model and operation ledger**

Add to TrainingInputRecorder:

~~~python
operation_id: Optional[str] = Field(
    default=None,
    description="System-owned idempotency key for an approved write",
)
~~~

Create write_operations in init_db and lazily in add_training_session for existing production databases:

~~~sql
CREATE TABLE IF NOT EXISTS write_operations (
    operation_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
~~~

Before business inserts, INSERT OR IGNORE the marker inside the same transaction. If cursor.rowcount is zero, SELECT and return the stored result. If later inserts fail, rollback removes the new marker and business rows together.

- [ ] **Step 5: Inject a stable ID only after pure approval**

Copy each executable call and args. For log_training_session only:

~~~python
executable_call["args"]["operation_id"] = f"hitl:{executable_call['id']}"
~~~

Overwrite any model-supplied operation ID because this field is system-owned.
Do not add it to log_meal, read tools, revised calls, or rejected calls.

- [ ] **Step 6: Run tests and verify GREEN**

Run uv run pytest tests/test_sqlite_handler.py tests/test_safe_execution.py -q. Expected: all focused tests pass without warnings.

- [ ] **Step 7: Commit**

~~~bash
git add agents/models.py agents/sqlite_handler.py tools/safe_execution.py tests/test_sqlite_handler.py tests/test_safe_execution.py
git commit -m "fix: make approved training writes idempotent"
~~~

---

### Task 4: Lock the Multi-Turn Contract and Update Documentation

**Files:**
- Modify: agents/roles/training.py
- Modify: tests/test_safe_execution.py
- Modify: README.md
- Modify: docs/index.html

**Interfaces:**
- Consumes: superseded ToolMessage followed by revision HumanMessage.
- Guarantees: the agent rebuilds the complete draft and the replacement call triggers fresh approval.

- [ ] **Step 1: Write the failing prompt-contract test**

Render the recording prompt and assert it contains “superseded”, “merge”, and “new approval”. The production change that makes this pass is the explicit revision rule below.

- [ ] **Step 2: Add the training-agent revision rule**

Add:

~~~text
If a previous log_training_session call was superseded because the user added,
corrected, or removed information while replying to an approval request, merge
the complete revision into the previous draft. Rebuild the full tool arguments
and call log_training_session again. The replacement call requires a new
approval. Never claim that the superseded call was saved.
~~~

- [ ] **Step 3: Add the deterministic state-transition regression**

Using real SafeToolNode behavior plus fake resolvers, assert:

1. the original call has rpe=None;
2. mixed approval resolves to revise and executes zero writes;
3. the returned messages contain the superseded ToolMessage and raw HumanMessage;
4. a replacement call with rpe=7 interrupts again;
5. pure approval executes that replacement exactly once with operation_id="hitl:training-revised".

Name it test_training_revision_requires_fresh_approval_before_single_write.

- [ ] **Step 4: Run regression tests**

Run uv run pytest tests/test_safe_execution.py tests/test_api.py -q. Expected: all pass without warnings.

- [ ] **Step 5: Update documentation**

Add this behavior to README.md and the corresponding section of docs/index.html:

~~~text
如果确认回复同时补充或修改了训练信息，ChatFit 不会立即写入；它会更新待保存内容并再次请求确认。确认后的重复投递使用幂等键，不会生成重复训练记录。
~~~

Do not change unrelated layout or architecture content.

- [ ] **Step 6: Run full local verification**

Run:

~~~bash
uv run pytest -q
make quality
git diff --check
~~~

Expected: tests pass; Ruff, Black, MyPy, and Bandit report no errors, failures, or warnings; git diff --check prints nothing.

- [ ] **Step 7: Commit**

~~~bash
git add agents/roles/training.py tests/test_safe_execution.py README.md docs/index.html
git commit -m "test: cover approval revisions across turns"
~~~

---

### Task 5: Independent Quality Verification and Fix Loop

**Files:**
- Read: docs/quality.md
- Verify: all modified production, test, and documentation files

**Interfaces:**
- Produces: independent verification with commands, exit codes, errors, failures, and warnings.

- [ ] **Step 1: Spawn the required verifier**

Use this assignment:

~~~text
In /Users/hjw/Projects/ChatFit/.worktrees/fix-hitl-context, independently verify
the HITL approval-revision fix. Read docs/quality.md and follow it exactly. Run
make quality and the full non-E2E pytest suite, inspect README.md and
docs/index.html for consistency, run git diff --check, and report every error,
failure, or warning. Do not modify files.
~~~

- [ ] **Step 2: Fix every finding**

For behavior changes, first add or adjust the smallest failing test, verify RED, implement the correction, and verify GREEN. For formatting or documentation findings, make the minimal correction and rerun the failed check.

- [ ] **Step 3: Repeat independent verification until pristine**

Required final evidence:

~~~text
make quality: exit 0, no warnings
uv run pytest -q: exit 0, no failures, no warnings
git diff --check: exit 0, no output
README.md and docs/index.html: consistent with behavior
~~~

- [ ] **Step 4: Commit verification fixes if needed**

Stage only files changed for verifier findings and commit with message “fix: address independent verification findings”. Skip this commit when there are no findings and the worktree is clean.
