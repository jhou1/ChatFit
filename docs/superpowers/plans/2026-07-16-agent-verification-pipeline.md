# Agent Verification Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a robust, Makefile-based verification pipeline to enforce code quality, type safety, test coverage, and security for agent outputs.

**Architecture:** A central `Makefile` acts as the interface, chaining together standard Python code quality tools. `AGENTS.md` is updated to instruct agents to use the pipeline.

**Tech Stack:** GNU Make, Black, Ruff, MyPy, Pytest, Pytest-Cov, Bandit.

---

### Task 1: Create the Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write the Makefile content**

```makefile
.PHONY: format lint typecheck test security coverage verify

format:
	black .
	ruff check --fix .

lint:
	ruff check .
	black --check .

typecheck:
	mypy .

test:
	pytest

security:
	bandit -r .

coverage:
	pytest --cov=. --cov-report=term-missing

verify: lint typecheck security test
	@echo "All verification checks passed."
```

- [ ] **Step 2: Verify `make verify` executes (even if tests fail)**

Run: `make verify`
Expected: The pipeline should attempt to run `lint`, `typecheck`, `security`, and `test` sequentially. It might fail depending on current project state, but the structure is verified.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build: add Makefile for agent verification pipeline"
```

---

### Task 2: Update AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Append pipeline instructions to AGENTS.md**

```bash
cat << 'EOF' >> AGENTS.md

# Agent Verification Pipeline
Before asserting that any task is complete, you MUST run `make verify`. You are only allowed to claim success if this command passes with a 0 exit code. If it fails, read the error output, fix the errors, and try again.
EOF
```

- [ ] **Step 2: Verify the modification**

Run: `cat AGENTS.md | grep "make verify"`
Expected: Prints the line containing `make verify`.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add pipeline instructions to AGENTS.md"
```

---

### Task 3: Ensure pipeline tools are available

**Files:**
- Create: `tests/test_dummy.py` (temporary to verify pytest works)

- [ ] **Step 1: Create a simple dummy test**

```python
# tests/test_dummy.py
def test_pipeline_sanity():
    assert True
```

- [ ] **Step 2: Run individual Makefile targets to verify tool availability**

Run: `make lint`
Expected: Succeeds or outputs lint errors (proving tool exists)

Run: `make typecheck`
Expected: Succeeds or outputs type errors (proving tool exists)

Run: `make security`
Expected: Succeeds or outputs bandit alerts (proving tool exists)

Run: `make test`
Expected: Runs `test_dummy.py` and other existing tests.

- [ ] **Step 3: Remove dummy test and commit**

```bash
rm tests/test_dummy.py
git commit --allow-empty -m "chore: verified pipeline tools are installed and operational"
```
