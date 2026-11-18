# Glint developer commands. `make help` lists them.
PYTHON ?= .venv/bin/python
VERSION := $(shell tr -d '[:space:]' < VERSION)
DMG := dist/Glint-$(VERSION)-arm64.dmg
MIN_MACOS ?= 13.4
SWIFT_FRAMEWORKS := -framework AppKit -framework WebKit -framework Carbon -framework AVFoundation \
	-framework Speech -framework ServiceManagement -framework UserNotifications

.PHONY: help setup build run package dmg test check demo clean

help:
	@echo "make setup    create .venv with the engine's Python dependencies"
	@echo "make build    dev build: build/Glint.app running the engine from this checkout"
	@echo "make run      dev build, then open it"
	@echo "make package  self-contained dist/Glint.app (embedded Python, no .venv needed)"
	@echo "make dmg      dist/Glint-$(VERSION)-arm64.dmg from the packaged app, verified"
	@echo "make test     engine tests (memory + retrieval; downloads the ~90 MB model once)"
	@echo "make check    static checks: Python compile, node --check, swiftc -typecheck"
	@echo "make demo     run scripts/demo.sh"
	@echo "make clean    remove dist/"

setup:
	scripts/setup.sh

build:
	scripts/build.sh

run:
	scripts/build.sh --open

package:
	scripts/package.sh

dmg: package
	scripts/make-dmg.sh

test:
	$(PYTHON) -W ignore engine/tests/test_memory.py
	$(PYTHON) -W ignore engine/tests/test_retrieval.py

check:
	$(PYTHON) -m py_compile engine/*.py engine/features/*.py
	@for f in $$(find web -name '*.js'); do node --check "$$f" || exit 1; done; echo "node --check: ok"
	swiftc -typecheck -target arm64-apple-macos$(MIN_MACOS) mac/*.swift $(SWIFT_FRAMEWORKS)
	swiftc -typecheck -target arm64-apple-macos$(MIN_MACOS) mac/capture/GlintCapture.swift
	@if [ -d "$$(xcrun --show-sdk-path)/System/Library/Frameworks/FoundationModels.framework" ]; then \
		swiftc -typecheck -target arm64-apple-macos26.0 mac/llm/GlintLLM.swift && echo "glint-llm: ok"; \
	else echo "glint-llm: skipped (SDK has no FoundationModels; needs Xcode 26)"; fi
	@echo "swiftc -typecheck: ok"

demo:
	@if [ -x scripts/demo.sh ]; then scripts/demo.sh; \
	elif [ -f scripts/demo.sh ]; then bash scripts/demo.sh; \
	else echo "scripts/demo.sh not found"; exit 1; fi

clean:
	rm -rf dist
