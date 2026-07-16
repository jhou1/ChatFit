# Agent Verification Pipeline Design Spec

## Overview
A `Makefile`-based verification pipeline designed to ensure high-quality code output from autonomous AI coding agents. It enforces project standards by running formatters, linters, type checkers, security scanners, and unit tests in a fail-fast sequence. 

## Goals
- Prevent agents from committing or finalizing tasks with syntax errors, type errors, or failing tests.
- Provide a single, unified command (`make verify`) that the agent can reliably call.
- Surface exact error messages to the agent's standard output so it can self-correct.

## Architecture & Components
The core interface is a `Makefile` placed at the root of the project.

### Makefile Targets
- `make format`: Automatically formats code using `black .` and `ruff check --fix .`.
- `make lint`: Enforces style without modifying files using `ruff check .` and `black --check .`.
- `make typecheck`: Statically verifies types using `mypy .`.
- `make test`: Runs the test suite using `pytest`.
- `make security`: Scans for common vulnerabilities using `bandit -r .`.
- `make coverage`: Checks test coverage using `pytest --cov=. --cov-report=term-missing`.
- **`make verify`**: The master target. Runs `lint`, `typecheck`, `security`, and `test` sequentially. It fails immediately if any step returns a non-zero exit code.

### Agent Instructions (AGENTS.md)
The existing `AGENTS.md` file will be updated to include strict instructions:
> "Before asserting that any task is complete, you MUST run `make verify`. You are only allowed to claim success if this command passes with a 0 exit code. If it fails, fix the errors and try again."

## Data Flow
1. Agent completes a code modification.
2. Agent executes `make verify`.
3. If successful (exit code 0), the agent proceeds to finalize the task.
4. If a failure occurs (exit code > 0), the `Makefile` stops execution and prints the standard error/output of the failing tool (e.g., `ruff` or `pytest`).
5. The agent reads the output, uses its tools to fix the offending code, and repeats from step 2.

## Dependencies
- `black`
- `ruff`
- `mypy`
- `pytest`
- `pytest-cov`
- `bandit`
(Note: Ensure these are available in the project's dependency manager, e.g., `pyproject.toml` or `requirements.txt`).
