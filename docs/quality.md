# Quality

## Code Quality
This project makes use of the following tools to ensure clean, consistent coding style and production grade quality.
- `MyPy` for static type checking
- `Ruff` for linting and formatting
- `Black` for consistent code formatting
- `Bandit` for static security analysis

Refer to `Makefile` to understand how these tools are used.

IMPORTANT: You must run `make quality` before concluding job done. If you find error, warning or the command exited with non-zero exit code, you must report it back. You are not responsible for fixing the error. You do run, test, verify and report.

## Doc Quality
When review code, make sure the `README.md`, `docs/index.html` is updated accordingly. If you find code is ahead but docs are staled, report failure.
