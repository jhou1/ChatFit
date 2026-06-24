# Pylint Pre-Commit Hook Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate local Python code linting in the ChatFit project using pylint managed by pre-commit.

**Architecture:** Use `uv` for dependency management and `pre-commit` local hooks to execute `pylint` within the project's dependency environment.

**Tech Stack:** python, uv, pre-commit, pylint

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Install dev dependencies**
Run: `uv add --dev pre-commit pylint`
Expected: Success message indicating packages were added.

- [ ] **Step 2: Commit changes**
```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pre-commit and pylint as dev dependencies"
```

### Task 2: Configure pre-commit hook

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Create hook configuration**
Create `.pre-commit-config.yaml` with the following content:
```yaml
repos:
  - repo: local
    hooks:
      - id: pylint
        name: pylint
        entry: uv run pylint
        language: system
        types: [python]
        require_serial: true
```

- [ ] **Step 2: Commit configuration**
```bash
git add .pre-commit-config.yaml
git commit -m "ci: configure local pylint pre-commit hook"
```

### Task 3: Install and run hook

**Files:**
- Modify: `.git/hooks/pre-commit` (implicitly)

- [ ] **Step 1: Install pre-commit hook**
Run: `uv run pre-commit install`
Expected: "pre-commit installed at .git/hooks/pre-commit"

- [ ] **Step 2: Run hook on all files**
Run: `uv run pre-commit run --all-files || true`
Expected: Pylint executes and checks files (might fail if there are existing lint issues, which is fine for the setup step).

