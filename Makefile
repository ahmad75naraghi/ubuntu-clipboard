.PHONY: help install uninstall run background status test lint format check gtk-check clean

# Use the project virtualenv when one exists, otherwise the system interpreter.
# Override explicitly with: make test PYTHON=/path/to/python
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install for the current user (packages, package, integration)
	./scripts/install.sh

uninstall: ## Remove the user level integration (add --purge to delete data)
	./scripts/uninstall.sh

run: ## Open the clipboard window
	$(PYTHON) -m ubuntu_clipboard --toggle

background: ## Run the clipboard history in the background
	$(PYTHON) -m ubuntu_clipboard --background

status: ## Show the environment, database and shortcut state
	$(PYTHON) -m ubuntu_clipboard --status

test: ## Run the test suite (headless, no display needed)
	$(PYTHON) -m pytest

lint: ## Static analysis and formatting check
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format: ## Apply automatic formatting
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

gtk-check: ## Verify the GTK/GDK/Adw API surface (needs PyGObject-stubs)
	$(PYTHON) scripts/check_gtk_api.py --verbose

check: lint test ## Lint and test

clean: ## Remove build artefacts and caches
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
