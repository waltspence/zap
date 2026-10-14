.PHONY: check test lint type gui-check app
check: lint type test
test:
	python -m pytest -q
lint:
	python -m ruff check zap tests
type:
	python -m mypy zap

gui-check:  ## build the native macOS app package and run its checks
	cd macos/Zap && swift build && swift run zapkit-check

app:  ## build ~/Applications/Zap.app (macOS, needs Command Line Tools)
	python scripts/make_macos_app.py
