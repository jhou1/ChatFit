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
