# HITL Approval Amendments and Idempotent Training Writes

## Problem

When a training write is paused for human approval, the API currently treats the
entire next user message as an approval or rejection. A reply such as
`保存，同时 RPE 7` approves the pending write but also supplies a missing field.
The approval path discards that field, executes the original tool arguments, and
does not append the reply to conversation history. The agent can then ask for RPE
again and issue a second write. Because training inserts are not idempotent, the
second call creates a duplicate record.

## Decision

Implement structured approval amendments at the HITL boundary and add an
idempotency key to training writes.

The approval classifier receives both the user's reply and the pending tool
calls. It returns one of four intents:

- `approve`: execute the pending write unchanged.
- `approve_with_patch`: merge an allowlisted, typed patch into the pending write
  and execute the resulting arguments.
- `reject`: reject the pending write and preserve the user's feedback.
- `new_request`: reject the pending write and preserve the message for a future
  request. The current change does not automatically dispatch that future
  request.

For this iteration, amendment patches support the optional training-session
fields `rpe`, `warm_up`, and `cool_down`. Patches identify a session by its
zero-based position in the pending `sessions` list. Unknown fields, invalid
indices, or values that fail `TrainingInputRecorder` validation cause a safe
rejection; the original write is not executed.

The user's explicit approval applies to the merged draft. Therefore
`保存，同时 RPE 7` performs one write in the same turn without requesting a
second confirmation.

## Alternatives Considered

### Prompt-only context guidance

Add instructions telling the training agent to merge confirmation replies into
the pending record. This cannot work reliably because the paused graph consumes
the reply in the API layer before the training agent sees it. Prompt changes may
improve ordinary clarification turns but cannot repair this data-flow break.

### Cancel and restart the graph with the reply

Reject the pending write, append the reply as a new human message, and ask the
agent to build a replacement tool call. This avoids mutating a paused call but
usually produces a second approval prompt and remains dependent on model
inference. It is safe but gives a worse experience for a reply that explicitly
approves the amended record.

### General persisted draft repository

Introduce a generic draft state machine and repository for every writable agent.
This is a useful future direction, especially for multimodal clarification, but
is larger than needed for this production defect. The structured HITL decision
provides a bounded seam that can later be backed by a draft repository.

## Components

### Approval decision model

Add Pydantic models for the classifier output:

- `ApprovalIntent`: the four literal intent values.
- `TrainingSessionPatch`: `session_index` plus optional `rpe`, `warm_up`, and
  `cool_down` values.
- `ApprovalDecision`: intent, feedback, and a list of training patches.

The classifier prompt must require strict JSON and include a concise view of the
pending tool calls. Parsing or validation failures produce a `reject` decision.
Raw message content remains excluded from observability attributes.

### Patch application

Create a deterministic helper that takes pending tool calls and an
`ApprovalDecision`, copies the arguments, applies allowlisted patches, and
validates amended `log_training_session` arguments with
`TrainingInputRecorder`.

The helper returns amended tool calls without mutating checkpoint-owned message
objects. The API includes those amended calls in the resume payload. The safe
tool node executes the amended calls only when the decision is approved; pure
approvals continue to execute the checkpointed calls.

### Conversation continuity

The resume payload includes the user's reply. After executing an approved write,
the safe tool node emits the tool outputs followed by a `HumanMessage` containing
that reply, so the tool-call protocol remains valid and subsequent LLM reasoning
sees the supplied RPE. Rejection keeps the existing error `ToolMessage` feedback
behavior.

### Idempotent training write

Add an optional `operation_id` to `TrainingInputRecorder`. The API derives a
stable operation ID from the interrupted tool call ID and injects it into the
amended or unchanged call before execution.

Persist operation IDs in a lazily created `write_operations` table with a unique
primary key. Lazy creation makes the change compatible with existing production
databases that are not passed through `init_db`. Within the same SQLite
transaction:

1. insert the operation ID for `log_training_session`;
2. if it already exists, return the prior successful result without inserting
   sessions or sets;
3. otherwise insert all training data and commit both the operation marker and
   business records atomically.

Direct callers that omit `operation_id` preserve the existing non-idempotent
behavior for backward compatibility. New HITL writes always receive one.

## Data Flow

1. The training agent creates `log_training_session` arguments and pauses at
   `SafeToolNode`.
2. The API reads the pending tool calls and classifies the user reply.
3. For `approve_with_patch`, the API applies and validates the patch.
4. The API resumes the interrupt with the decision, amended calls, reply, and
   stable operation IDs.
5. `SafeToolNode` substitutes amended calls by matching tool call ID, adds the
   reply to message history, and executes each approved call once.
6. SQLite atomically records the operation and training rows. A replay with the
   same operation ID returns success without adding rows.

## Error Handling

- Classifier timeout, malformed JSON, or schema failure: reject safely.
- Patch references a missing session or unsupported tool: reject safely.
- Patch fails `TrainingInputRecorder` validation: reject safely and expose a
  concise explanation to the agent through the tool error message.
- Operation marker insert succeeds but a business insert fails: transaction
  rollback removes both.
- A repeated successful operation ID: return the original success message with
  no additional rows.

## Tests

Add deterministic tests for:

- classifier parsing of `保存，同时 RPE 7` into `approve_with_patch`;
- API resume payload containing amended RPE, user reply, and stable operation ID;
- safe tool execution substituting the amended call and preserving the human
  reply in graph history;
- invalid amendments never executing the original write;
- two `add_training_session` calls with the same operation ID creating one
  session and one set collection;
- calls without an operation ID retaining existing behavior;
- the full regression shape asserting one write and `rpe = 7`.

Run focused tests first, then the full non-E2E suite and `make quality`. An
independent verification agent must run the quality instructions in
`docs/quality.md` before completion.

## Documentation

Update `README.md` and `docs/index.html` only if their public behavior
description would otherwise become inaccurate. The existing documentation says
writes require approval; that remains true. Add a concise note that an approval
reply may amend the pending record and that retries are idempotent.

## Acceptance Criteria

- `保存，同时 RPE 7` executes exactly one training write.
- The saved training session has `rpe = 7`.
- The agent does not ask for RPE again for that committed session.
- Replaying the same approved interrupt does not add database rows.
- Pure approval and rejection behavior remain compatible.
- All tests and static-quality checks complete with no errors, failures, or
  warnings.
