# HITL Approval Revisions and Idempotent Training Writes

## Problem

When a training write is paused for human approval, the API currently treats the
entire next user message as an approval or rejection. A reply such as
`保存，同时 RPE 7` approves the pending write but also supplies a missing field.
The approval path discards that field, executes the original tool arguments, and
does not append the reply to conversation history. The agent can then ask for RPE
again and issue a second write. Because training inserts are not idempotent, the
second call creates a duplicate record.

## Decision

Implement approval-aware revision handling at the HITL boundary and add an
idempotency key to training writes.

An approval is valid only for the exact draft shown to the user. The approval
resolver receives both the user's reply and the pending tool calls and returns
one of three intents:

- `approve`: the reply is a pure approval, so execute the pending write
  unchanged.
- `revise`: the reply contains any new, corrected, or removed business data.
  Supersede the pending write, send the complete reply back to the agent, build a
  revised draft, and request approval again.
- `reject`: reject the pending write and preserve the user's feedback.

Any additional information invalidates the old approval, including optional
fields such as `rpe`, `warm_up`, or `cool_down`, corrections to an existing
value, removal requests, or an additional practice. The resolver does not merge
individual fields. The training agent receives the complete revision and
rebuilds typed tool arguments from the existing conversation plus the new
information.

Therefore `保存，同时 RPE 7` performs no write in that turn. The agent adds RPE
7 to the pending record, presents the revised record, and asks for approval
again. A subsequent pure `保存` executes the revised write exactly once.

## Alternatives Considered

### Prompt-only context guidance

Tell the training agent to merge confirmation replies into the pending record.
This cannot work reliably while the API consumes the reply before the training
agent sees it. Prompt changes cannot repair that data-flow break by themselves.

### Approve and mutate the paused call

Merge new fields into the paused call and treat the same reply as approval of
the result. This is efficient, but it permits a write whose final representation
was never shown to the user. It does not satisfy the requirement that every
changed draft receive fresh approval.

### General persisted draft repository

Introduce a generic draft state machine and repository for every writable agent.
This remains a useful future direction, especially for multimodal clarification,
but is larger than needed for this defect. The approval resolver creates a
bounded seam that can later be backed by a persisted draft repository.

## Components

### Approval resolver

Add an injectable LLM-backed resolver with a narrow interface:

```python
class ApprovalResolver:
    async def resolve(
        self,
        user_message: str,
        pending_tool_calls: list[dict],
    ) -> ApprovalDecision: ...
```

`ApprovalDecision` contains
`intent: Literal["approve", "revise", "reject"]` and `feedback: str`. The
resolver prompt requires strict JSON and includes a concise view of the pending
tool calls. Parsing or validation failures produce a `reject` decision. Raw
message content remains excluded from observability attributes.

The API no longer interprets the reply as business data. It passes the raw reply
into the graph's resume payload. `SafeToolNode` invokes the resolver because it
owns the exact pending tool calls and the execution boundary.

### Revision handling

For `revise`, `SafeToolNode` must not execute any pending write. It returns one
error `ToolMessage` per superseded write call, followed by a `HumanMessage`
containing the user's complete revision. This ordering satisfies the tool-call
protocol and lets the training agent see the additional information.

The training agent merges the revision with the prior draft, validates the
result through `TrainingInputRecorder`, and issues a replacement
`log_training_session` call. That replacement reaches `SafeToolNode` as a new
pending write and always triggers a new approval request. Approval from a
superseded call is never carried to a replacement call.

Pure approval may execute without adding a redundant human message because it
contains no new business context. Rejection keeps the existing error
`ToolMessage` feedback behavior.

### Idempotent training write

Add an optional `operation_id` to `TrainingInputRecorder`. `SafeToolNode` derives
a stable operation ID from the approved tool call ID and injects it immediately
before execution. Superseded calls receive no operation ID because they never
write.

Persist operation IDs in a lazily created `write_operations` table with a unique
primary key. Lazy creation supports existing production databases that are not
passed through `init_db`. Within the same SQLite transaction:

1. insert the operation ID for `log_training_session`;
2. if it already exists, return the prior successful result without inserting
   sessions or sets;
3. otherwise insert all training data and commit both the operation marker and
   business records atomically.

Direct callers that omit `operation_id` preserve existing behavior for backward
compatibility. Executed HITL writes always receive one.

## Data Flow

1. The training agent creates `log_training_session` arguments and pauses at
   `SafeToolNode`.
2. The API resumes the interrupt with the complete raw user reply.
3. `SafeToolNode` resolves the reply against the exact pending tool calls.
4. For pure approval, the node adds stable operation IDs and executes the
   unchanged calls.
5. For a revision, the node executes no writes, marks the old calls superseded,
   and adds the reply to graph history.
6. The training agent produces revised typed arguments, which create a new
   approval interrupt.
7. SQLite records the operation and training rows only after a later pure
   approval. Replaying that operation ID adds no rows.

## Error Handling

- Resolver timeout, malformed JSON, or schema failure: reject safely.
- A reply classified as `revise`: execute no writes, even when it also contains
  approval language.
- Revised arguments fail `TrainingInputRecorder` validation: ask for
  clarification and do not produce an executable write.
- Operation marker insert succeeds but a business insert fails: roll back both.
- A repeated successful operation ID: return success with no additional rows.

## Tests

Add deterministic tests for:

- resolving `保存，同时 RPE 7` as `revise`;
- the API resume payload preserving the complete reply without interpreting it;
- revision handling executing no writes and preserving the reply in history;
- the training agent producing replacement arguments with RPE 7;
- the replacement call creating a second approval interrupt;
- a later pure approval executing the replacement once;
- two `add_training_session` calls with one operation ID creating one session
  and one set collection;
- calls without an operation ID retaining existing behavior;
- the full regression shape asserting zero writes after the mixed reply and one
  write with `rpe = 7` after the second approval.

Run focused tests first, then the full non-E2E suite and `make quality`. An
independent verification agent must run the quality instructions in
`docs/quality.md` before completion.

## Documentation

Update `README.md` and `docs/index.html` with a concise note that any amendment
supersedes pending approval, requires fresh approval, and remains idempotent
after execution.

## Acceptance Criteria

- `保存，同时 RPE 7` executes no training write.
- The agent presents a revised draft containing `rpe = 7` and asks again.
- A later pure approval creates exactly one training session with `rpe = 7`.
- The agent does not ask for RPE again after that session is committed.
- Replaying the approved interrupt does not add database rows.
- Pure approval and rejection behavior remain compatible.
- All tests and static-quality checks complete with no errors, failures, or
  warnings.
