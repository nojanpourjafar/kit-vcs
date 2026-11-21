# Shortcuts for working on Kit. Run `make` on its own to list them.
# (Recipe lines in a Makefile must start with a tab, not spaces.)

PYTHON ?= python3

.PHONY: help test app dev ui build open icon release

help: ## list these shortcuts
	@awk -F':.*## ' '/^[a-z]+:.*## / {printf "  make %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test: ## run all the tests
	$(PYTHON) -m pytest -q

app: ## open Kit in its own window
	$(PYTHON) -m kit app

dev: ## open Kit for working on ui.html: reloads on save, right-click > Inspect Element
	$(PYTHON) -m kit app --dev

ui: ## open Kit in the browser, for the kit in this folder
	$(PYTHON) -m kit ui

build: ## build the double-click app into dist/
	$(PYTHON) packaging/build_app.py

open: build ## build the app, then open it
	open dist/Kit.app

icon: ## redraw the app icon after changing packaging/make_icon.py
	$(PYTHON) packaging/make_icon.py

release: ## publish a new version: make release VERSION=0.6.0
	@test -n "$(VERSION)" || { echo 'Say which version: make release VERSION=0.6.0'; exit 1; }
	$(PYTHON) scripts/release.py $(VERSION)
