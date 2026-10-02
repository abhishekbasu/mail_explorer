.DEFAULT_GOAL := dev

PORT ?= 8000

.PHONY: dev
dev:
	uv run uvicorn mail_explorer.app:app --host 127.0.0.1 --port "$(PORT)" --reload --reload-dir src
