.PHONY: format lint typecheck test security coverage verify

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
	uv run bandit -r .

coverage:
	uv run pytest --cov=. --cov-report=term-missing

verify: lint typecheck security test
	@echo "All verification checks passed."
