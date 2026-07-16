
# Quality
- Use `MyPy` for static type checking
- Use `Ruff` for linting and formatting
- Use `Black` for consistent code formatting

# Agent Verification Pipeline
Before asserting that any task is complete, you MUST run `make verify`. You are only allowed to claim success if this command passes with a 0 exit code. If it fails, read the error output, fix the errors, and try again.
