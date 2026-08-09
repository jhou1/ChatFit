# Deterministic Pure Approval Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bare `确认`, `保存`, and `确认保存` replies execute the exact pending write without a second approval prompt.

**Architecture:** Add a narrow deterministic predicate at the start of `ApprovalResolver.resolve`; matching replies return `approve` before the LLM is called, while every non-match keeps the existing LLM-backed approve/revise/reject flow. `SafeToolNode`, graph state, Agent prompts, and persistence remain unchanged.

**Tech Stack:** Python 3.13, LangChain messages, LangGraph interrupts, Pydantic v2, pytest, pytest-asyncio.

## Global Constraints

- Only `确认`, `保存`, and `确认保存` qualify for deterministic approval.
- Ignore surrounding whitespace and allow at most one trailing character from `。`, `.`, `！`, or `!`.
- Do not fast-path question marks, internal punctuation, additions, or corrections.
- Preserve the original user message in `ApprovalDecision.feedback`.
- Non-matching replies and malformed model output retain the existing behavior.
- Do not change API, Agent, graph, or database interfaces.

## File Structure

- `tools/safe_execution.py`: define the deterministic approval vocabulary and predicate; call it before LLM classification.
- `tests/test_safe_execution.py`: cover accepted variants, unsafe non-matches, no-LLM behavior, and pending-write execution.
- `README.md` and `docs/index.html`: inspect for consistency; no content change is expected because they already document confirmation and revised-draft behavior.

---

### Task 1: Deterministic Pure Approval Resolution

**Files:**
- Modify: `tools/safe_execution.py:15-65`
- Test: `tests/test_safe_execution.py:550-610`

**Interfaces:**
- Produces: `_is_pure_approval(user_message: str) -> bool`
- Preserves: `ApprovalResolver.resolve(user_message: str, pending_tool_calls: list[dict]) -> ApprovalDecision`
- Preserves: `ApprovalDecision(intent: Literal["approve", "revise", "reject"], feedback: str)`

- [ ] **Step 1: Add failing resolver tests for the deterministic approval contract**

Add imports or reuse the existing imports for `Mock`, `patch`, `AIMessage`, and
`pytest`, then add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    ["确认", "保存", "确认保存", " 确认 ", "保存。", "确认保存！"],
)
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_approval_resolver_deterministically_approves_pure_reply(
    mock_create_chat_model, mock_execute, reply
):
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve(reply, [{"name": "log_meal", "args": {}}])

    assert decision == ApprovalDecision(intent="approve", feedback=reply)
    mock_execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    ["确认，RPE 改成 7", "保存，同时 RPE 7", "确认？", "保存..."],
)
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_approval_resolver_sends_non_pure_reply_to_classifier(
    mock_create_chat_model, mock_execute, reply
):
    mock_execute.return_value = {"messages": AIMessage(content='{"intent":"revise"}')}
    resolver = ApprovalResolver(Mock())

    decision = await resolver.resolve(reply, [{"name": "log_meal", "args": {}}])

    assert decision == ApprovalDecision(intent="revise", feedback=reply)
    mock_execute.assert_awaited_once()
```

- [ ] **Step 2: Add a failing regression test for the reported state transition**

Add a test using the real resolver and the existing safe node boundary:

```python
@pytest.mark.asyncio
@patch("tools.safe_execution.interrupt")
@patch("tools.safe_execution._execute_single_tool_safely")
@patch("tools.safe_execution._execute_llm_query_safely")
@patch("tools.safe_execution.create_chat_model")
async def test_bare_confirmation_executes_pending_write_without_llm(
    mock_create_chat_model, mock_llm_query, mock_tool_execute, mock_interrupt
):
    mock_llm_query.return_value = {
        "messages": AIMessage(content='{"intent":"revise"}')
    }
    mock_interrupt.return_value = {"user_message": "确认"}
    mock_tool_execute.return_value = ToolMessage(
        content="Saved", tool_call_id="training-1"
    )
    node = SafeToolNode(tools=[], approval_resolver=ApprovalResolver(Mock()))
    pending = {
        "name": "log_training_session",
        "args": {"note": "test"},
        "id": "training-1",
    }

    result = await node({"messages": [AIMessage(content="", tool_calls=[pending])]})

    mock_llm_query.assert_not_awaited()
    mock_tool_execute.assert_awaited_once()
    assert result["messages"][0].content == "Saved"
```

The mocked classifier deliberately returns `revise`; before the fix, the node
does not execute the write, so the behavioral assertions fail for the reported
reason.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
uv run pytest \
  tests/test_safe_execution.py::test_approval_resolver_deterministically_approves_pure_reply \
  tests/test_safe_execution.py::test_approval_resolver_sends_non_pure_reply_to_classifier \
  tests/test_safe_execution.py::test_bare_confirmation_executes_pending_write_without_llm \
  -q
```

Expected: accepted-variant and state-transition tests fail because the current
resolver calls the mocked classifier and receives `revise`; the non-pure cases
pass through the classifier.

- [ ] **Step 4: Implement the minimal deterministic predicate**

Add near the HITL constants in `tools/safe_execution.py`:

```python
PURE_APPROVAL_REPLIES = frozenset({"确认", "保存", "确认保存"})
PURE_APPROVAL_TERMINATORS = frozenset({"。", ".", "！", "!"})


def _is_pure_approval(user_message: str) -> bool:
    normalized = user_message.strip()
    if normalized[-1:] in PURE_APPROVAL_TERMINATORS:
        normalized = normalized[:-1].rstrip()
    return normalized in PURE_APPROVAL_REPLIES
```

At the start of `ApprovalResolver.resolve`, before constructing the prompt or
serializing pending tool calls, add:

```python
if _is_pure_approval(user_message):
    return ApprovalDecision(intent="approve", feedback=user_message)
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 3. Expected: all parameter variants and the reported
state transition pass with no warnings.

- [ ] **Step 6: Run the complete safe-execution regression file**

Run:

```bash
uv run pytest tests/test_safe_execution.py -q
```

Expected: all tests pass with no errors, failures, or warnings.

- [ ] **Step 7: Verify documentation consistency**

Run:

```bash
rg -n "Human-in-the-loop|确认|approval" README.md docs/index.html
```

Confirm the existing text still accurately states that pure confirmation writes
the draft and data amendments require fresh confirmation. Do not modify public
documentation unless this check finds a contradiction.

- [ ] **Step 8: Commit the TDD implementation**

```bash
git add tools/safe_execution.py tests/test_safe_execution.py
git commit -m "fix: handle pure HITL approvals deterministically"
```

---

### Task 2: Full Verification and Independent Review

**Files:**
- Inspect: all tracked files selected by the project quality commands
- Inspect: `README.md`
- Inspect: `docs/index.html`

**Interfaces:**
- Consumes: the committed deterministic approval implementation from Task 1
- Produces: verification evidence with zero errors, failures, or warnings

- [ ] **Step 1: Run the full non-E2E suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass, with the configured E2E tests deselected and no
warnings.

- [ ] **Step 2: Run the mandatory static-quality gate**

Run:

```bash
make quality
```

Expected: Ruff, Black, MyPy, and Bandit all exit zero without warnings, ending
with `All static check passed.`

- [ ] **Step 3: Dispatch independent verification**

Give a fresh subagent the worktree path and require it to read
`docs/quality.md`, inspect `README.md` and `docs/index.html` for staleness, run
`make quality`, run the relevant regression and full test suites, and report
every error, failure, or warning. If it reports any issue, fix it with a new TDD
cycle and repeat independent verification with a fresh run until pristine.

- [ ] **Step 4: Record the final worktree state**

Run:

```bash
git status --short
git log -3 --oneline --decorate
```

Expected: the worktree is clean and the branch contains the design, plan, and
implementation commits.
