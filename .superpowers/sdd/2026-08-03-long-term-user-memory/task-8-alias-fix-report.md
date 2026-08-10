# Task 8 Alias Routing Fix Report

**Date:** 2026-08-09
**Base:** `a01f00d1f706f92242f5d7258ffba7b6d9987887`
**Worktree:** `/Users/hjw/Projects/ChatFit/.worktrees/long-term-memory`

## Finding and root cause

The Task 8 real-SQLite acceptance sequence could remember the canonical
`training_template:213` row but routed `把壶铃213更新成新的模板内容` to Chatter.
The shared command parser correctly authorized an `update`; however, its
marker-only automatic routing policy set `auto_route_memory=False` for the
alias-only target, and Supervisor discarded the router's `memory_agent` token
through `_ROUTABLE_AGENTS` before any owner-scoped target check.

The Memory Agent and store were not the failing components. Direct Memory Agent
coverage already proved that an alias update binds target and exact content to
the user message, keeps the row ID, increments its version, and does not trust
the interpreter's target or rewritten content.

## RED

Added
`test_alias_update_routes_only_for_exact_current_owner_memory` using a real
temporary `user-memory.db`, the compiled production graph, production store and
Memory Agent, and deterministic interpreter/router boundaries.

The test first remembered canonical `training_template:213` with aliases
`2-1-3` and `壶铃213`, then sent `把壶铃213更新成新的模板内容`. Before the production
change it failed at the observable graph boundary:

```text
assert updated["assistant_names"] == ["memory_agent"]
E AssertionError: assert ['chatter'] == ['memory_agent']
```

This was the expected feature-missing failure, after the remember step had
already proved one version-1 SQLite row with the exact explicit payload.

## Minimal fix

Supervisor keeps `memory_agent` outside the unconditional `_ROUTABLE_AGENTS`
allowlist. It accepts a router-selected memory route only when all of these
independent conditions hold:

1. the router returned the exact `memory_agent` token;
2. the shared deterministic parser authorized a command;
3. the command is a targeted update/forget whose canonical key or alias resolves
   to exactly one row in a fresh lookup under the current configured owner.

Unknown aliases, aliases owned only by another user, database lookup failures,
and ordinary unparsed messages therefore fail closed. The Memory Agent remains
the final Task 3 authorization boundary and reparses the original user text;
the LLM cannot choose the mutation target or replacement content.

## GREEN and regression evidence

The new regression passed and proved the complete sequence:

- remember produces exactly one canonical row at version 1;
- alias-only update selects only `memory_agent`;
- update keeps the original row ID, produces version 2, stores exact content
  `新的模板内容`, and leaves row count one;
- a new thread for the same user freshly loads the updated content;
- the same alias for another owner and an unknown alias select Chatter and do
  not mutate either owner;
- `忘掉壶铃213` selects Memory, physically removes the row, and cascades every
  owner alias.

The existing actual-graph regressions for
`修改今天的深蹲重量为100kg` and the meal equivalent still select only their
business specialist and leave deliberately colliding durable rows unchanged.

Focused result:

```text
77 passed in 2.19s
```

## Full gates

The first `make quality` correctly found that Black would reformat the two
changed Python files. After applying Black and restarting the complete gate:

```text
Ruff: All checks passed!
Black: 58 files left unchanged
MyPy: Success: no issues found in 58 source files
Bandit: No issues identified; 0 issues at every severity/confidence
All static check passed.
```

Full verification:

```text
403 passed, 3 deselected in 85.48s
All verification checks passed.
```

## Branch cleanliness

Removed the two trailing spaces from the approved design document's Date and
Status lines while retaining the readable field-per-line layout. The working
diff passes `git diff --check`. The required committed-range
`git diff main...HEAD --check` is rerun after the fix commit so it includes this
cleanup.

## Independent verification

Fresh verifier `/root/memory_task8_fix_alias/alias_fix_verifier` independently
read the architecture, quality policy, approved specification and plan, Task 8
brief, verification skill, and complete base-to-working-tree diff. It modified
no file and reported zero functional, quality, security-boundary, or stale-doc
finding.

Its independent evidence was:

```text
Focused Memory Agent + graph: 77 passed; no warning
make quality: exit 0; Ruff/Black/MyPy/Bandit clean; Bandit 0 issues
make verify: 403 passed, 3 deselected; no warning
git diff --check: exit 0
git diff main --check: exit 0
```

The verifier separately confirmed the four-way alias route constraint (shared
parser, router selection, configured owner, fresh unique SQLite resolution),
generic Training-only behavior, Task 3 target/content binding, owner isolation,
stable-row update, new-thread visibility, forget cascade, and documentation
freshness. It also confirmed the whitespace is absent from the working file;
the committed `main...HEAD` check follows the single fix commit.
