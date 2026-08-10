# SDD ledger — plan: docs/superpowers/plans/2026-08-03-long-term-user-memory.md

Branch base: 03b77146ba29bc4fe887f9cb4d6024874bad4449
Design commit: fa3f51f
Plan commit: ea030a8

Task 1: fix round 1/5 (3 addressed, 0 open — strengthened SQLite constraint, path, model, and normalization tests; commits 17dc830..3eb6267)
Task 1: complete (commits ea030a8..3eb6267, review clean)

Task 2: minor (deferred): concurrency test serializes through BEGIN IMMEDIATE and does not force the remember-time IntegrityError recovery branch; final whole-branch review must triage whether direct branch coverage is required.
Task 2: fix round 1/5 (1 addressed, 0 open — connections close after commit/rollback on success and failure; commits 6ea05e7..a004492)
Task 2: complete (commits 3eb6267..a004492, review clean; 1 deferred minor)

Task 3: fix round 1/5 (5 addressed, 0 open — authorization gate, immutable pending scope, exact update payload, atomic versioned forget, blank-payload completion; commits 85434da..dab9123)
Task 3: fix round 2/5 (2 addressed, 1 partial — direct target/source and pending candidate/source bound; zero-candidate recovery works but repeated confirmation can become update content; commits dab9123..c1a067b)
Task 3: fix round 3/5 (2 addressed, 0 open — bare confirmation rejected for every missing-content continuation; edge-only ASCII punctuation/paired quote support; commits c1a067b..11c39eb)
Task 3: complete (commits a004492..11c39eb, review clean after 3 fix rounds)

Task 4: fix round 1/5 (4 addressed, 1 partial — resume fresh-load, JSON pending, real two-turn completion, untrusted data closed; shared parser executes requested variants but graph auto-routing is over-broad; commits 29846f0..079e72a)
Task 4: fix round 2/5 (2 addressed, 0 open — authorization vs auto-route descriptor split; caplog plus recursive JSON primitive checkpoint assertion; commits 079e72a..83fb6d1)
Task 4: complete (commits 11c39eb..83fb6d1, review clean after 2 fix rounds)

Task 5: fix round 1/5 (2 addressed, 2 partial — trusted API auth/Bot wiring and DB separation added but malformed Bearer/file URI gaps remain; blank IDs and global test cleanup closed; commits 9edd83e..44a19f7)
Task 5: fix round 2/5 (2 addressed, 0 open — strict single-header token68 Bearer contract and all unsupported SQLite file URIs rejected; commits 44a19f7..063b879)
Task 5: complete (commits 83fb6d1..063b879, review clean after 2 fix rounds)

Task 6: fix round 1/5 (3 addressed, 2 partial — immutable/dry-run side effects, atomic reconcile/concurrency, safe errors closed; marker grammar still blacklist-based and parent-dir/writable-bind path race remain; commits ede306a..65d3f0d)
Task 6: fix round 2/5 (2 addressed, 0 original open — anchored approved marker and existing-parent dirfd/inode binding closed; commits 65d3f0d..61e98a6)
Task 6: fix round 3/5 (1 addressed, 0 open — missing parents fail closed with no mkdir and component-wise no-follow anchoring; commits 61e98a6..21eb9be)
Task 6: complete (commits 063b879..21eb9be, review clean after 3 original-review fix rounds and independent hardening)

Task 7: fix round 1/5 (3 addressed, 2 partial — connection closure, scoped memory assertions, and observable trajectories closed; landing disclosure understates all-memory prompt injection and README commands do not fail closed/load required Google key; commits 5cae211..55da452)
Task 7: fix round 2/5 (1 addressed, 1 partial — all-memory model/tracing disclosure closed; standalone parameter guards still continue in interactive shells; commits 55da452..b2628cc)
Task 7: fix round 3/5 (1 addressed, 0 open — interactive Bash/Zsh compound guards block curl/uvicorn for omitted or empty secrets; commits b2628cc..a01f00d)
Task 7: complete (commits 21eb9be..a01f00d, review clean after 3 fix rounds)

Task 8: fix round 1/5 (alias authorization routes only exact, uniquely resolved current-owner update/forget targets; commit dceb37b)
Task 8: fix round 2/5 (all runtime findings addressed — explicit fan-in fixes composite state writes; unrelated routes fail open while explicit mutations fail closed; unavailable state is distinct from confirmed-empty memory; final refresh is the single Memory response emitter for API/CLI/evaluation and interrupt/resume; IR_04 uses Memory Agent plus an exact real-SQLite assertion; architecture updated; commit 5096e44)
Task 8: fix round 3/5 (late strict-warning finding addressed — production and branch-related test SQLite connections close explicitly without warning suppression; commit fe387b6)
Task 8: complete (commits a01f00d..fe387b6; every alias/runtime/data-contract/streaming/ResourceWarning finding addressed; final `make quality` clean; final `make verify` 423 passed, 3 deselected; relevant branch-wide `-W error` gate 266 passed with 0 warnings; final verifier READY)

Whole-branch review fix wave: verifier round 1 NOT READY (dry-run returned zero for an active-WAL canonical content conflict and for a checkpointed alias collision; no other definite finding; commit range 7463fcb..3472803)
Whole-branch review fix wave: fix round 1 complete, independent re-verification pending (3 Important + 1 Minor original findings plus dry-run parity addressed — sidecars fail closed without file-family changes; checkpointed dry-run validates canonical content and every requested alias before apply; commits 460881c, a9894a4; report `whole-branch-fix-wave-report.md`; `make quality` clean; `make verify` 435 passed, 3 deselected; relevant `-W error` 187 passed with 0 warnings)
