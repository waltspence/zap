.PHONY: check test lint type
check: lint type test
test:
	python -m pytest -q
lint:
	python -m ruff check zap tests
type:
	python -m mypy zap
