# Deterministic Pure Approval Handling

## Problem

ChatFit pauses training and meal writes until the user approves the exact draft.
The approval resolver currently sends every reply, including a bare `确认` or
`保存`, to an LLM classifier. That makes an unambiguous approval probabilistic.
If the model labels a bare approval as `revise`, `SafeToolNode` supersedes the
pending write, sends the word back to the Agent, and the Agent builds the same
draft and asks for approval again. This matches the reported Telegram behavior.

The existing tests mock the resolver result for `保存`; they do not prove that
the real resolver treats `确认` or `保存` deterministically.

## Decision

Add a narrow deterministic fast path to `ApprovalResolver.resolve`. A reply that
contains only one of these approval phrases is immediately classified as
`approve`:

- `确认`
- `保存`
- `确认保存`

The comparison ignores surrounding whitespace and permits at most one trailing
declarative punctuation character from `。`, `.`, `！`, or `!`. It does not
ignore question marks, internal punctuation, or any other text.

All other replies keep the existing LLM-backed classification. In particular,
`确认，RPE 改成 7` and `保存，同时 RPE 7` are not fast-path approvals. They
remain revisions, so the old draft is superseded and the revised draft requires
fresh approval.

## Alternatives Considered

### Prompt and example changes only

Adding examples for `确认` and `保存` to the classifier prompt would improve the
odds but would leave an unambiguous safety decision dependent on nondeterministic
model output. It would not provide a stable regression guarantee.

### Fully deterministic natural-language parsing

Replacing the LLM with a large approval/revision/rejection ruleset would remove
model variability but would be difficult to maintain across languages and novel
ways of amending a draft. The reported defect does not justify that scope.

### Deterministic narrow approval with LLM fallback

This is the selected approach. It guarantees the explicitly requested phrases,
preserves flexible handling for other language, and keeps the safe fallback for
ambiguous or malformed model output.

## Components and Data Flow

Only `tools/safe_execution.py` changes in production code.

1. `ApprovalResolver.resolve` receives the raw reply and pending tool calls.
2. A small pure function normalizes and checks the reply against the narrow
   approval contract.
3. A match returns `ApprovalDecision(intent="approve", feedback=user_message)`
   without invoking the LLM.
4. A non-match follows the current prompt, validation, and safe-rejection path.
5. `SafeToolNode` continues to own execution. Approved pending calls retain
   their existing idempotency key and execute exactly once.

No API, Agent prompt, graph state, database schema, or Telegram behavior outside
the approval boundary changes.

## Safety and Error Handling

- Additional text never qualifies for the deterministic approval path.
- `确认？` remains ambiguous and uses the existing classifier.
- Empty replies and unknown phrases use the existing classifier.
- LLM timeout, invalid JSON, or schema failure continues to reject safely.
- Approval plus changed business data continues to execute zero writes until a
  revised draft receives a later pure approval.

## Tests

Use TDD in `tests/test_safe_execution.py`:

- prove bare `确认` and `保存` resolve to `approve` without an LLM call;
- prove surrounding whitespace and one allowed trailing punctuation character
  do not prevent approval;
- prove `确认保存` is accepted;
- prove `确认，RPE 改成 7`, `保存，同时 RPE 7`, and `确认？` do not take the
  deterministic path;
- retain the existing revision, rejection, malformed-response, HITL execution,
  and idempotency tests.

Run the focused test file first, the full non-E2E suite, and `make quality`.
An independent verification Agent must repeat the checks required by
`docs/quality.md` and report no errors, failures, or warnings.

## Documentation

`README.md` already states that writes require confirmation and that a reply
which changes training data creates a revised draft requiring another approval.
`docs/index.html` describes the same user-facing confirmation behavior. This fix
makes implementation conform to that existing contract, so no public behavior
documentation change is required.

## Acceptance Criteria

- Replying only `确认`, `保存`, or `确认保存` executes the exact pending write
  without another confirmation prompt.
- The accepted whitespace and punctuation variants behave identically.
- Replies containing additions or corrections do not execute the old draft and
  still require fresh approval.
- The deterministic path does not call the LLM.
- Existing rejection, revision, safe failure, and idempotency behavior remains
  compatible.
- Focused tests, the complete non-E2E suite, and all static quality checks pass
  with no errors, failures, or warnings.
