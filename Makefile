.PHONY: format lint typecheck test security coverage verify eval eval-live

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
	uv run pytest tests/test_evaluation.py -v

eval-live:
	uv run pytest tests/eval -m e2e -v
