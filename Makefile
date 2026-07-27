.PHONY: format lint typecheck test security coverage verify eval

format:
	uv run black .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run black --check .

typecheck:
	uv run mypy .

test:
	uv run pytest

security:
	uv run bandit -r . -x ./.venv,./tests,./.worktrees -ll

quality: lint format typecheck security
	@echo "All static check passed."

verify: test
	@echo "All verification checks passed."

eval:
	PYTHONPATH=. uv run python evaluation/runner.py
