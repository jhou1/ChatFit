# Pylint Pre-Commit Hook Configuration

## Objective
Automate local Python code linting in the ChatFit project using `pylint` to ensure code quality before commits are finalized.

## Architecture
- **Dependency Manager**: `uv` (development group)
- **Hook Framework**: `pre-commit`
- **Linter**: `pylint`

## Design Details
1. **Tool Integration**: Both `pre-commit` and `pylint` will be added to the `pyproject.toml` as dev dependencies.
2. **Hook Configuration**: A `.pre-commit-config.yaml` file will be placed in the project root.
3. **Local Hook Strategy**: To ensure `pylint` has access to the project's installed dependencies (like `langchain` and `langgraph`), the hook will be configured as a `local` repository that executes `uv run pylint`. This prevents false positive "missing import" errors that often occur when `pylint` runs in an isolated `pre-commit` environment.

## File Changes
- `pyproject.toml` (updated via `uv add`)
- `uv.lock` (updated via `uv add`)
- `.pre-commit-config.yaml` (new file)

## Setup Commands
- `uv add --dev pre-commit pylint`
- `uv run pre-commit install`
