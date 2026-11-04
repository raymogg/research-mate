.PHONY: setup fetch embed stats bench push test services

# Run from this directory. The catalog lives in ./data/catalog.db (override with DATA_DIR=...).
export DATA_DIR ?= ./data
UV ?= $(shell command -v uv)

setup:
	uv sync
	uv run python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name() if torch.cuda.is_available() else '')"

# arXiv metadata, newest first back to 2005, then new papers every 3 hours. Until Ctrl-C.
fetch:
	uv run python -m collect fetch

# SPECTER2 embeddings on the GPU, newest first, then new papers every minute. Until Ctrl-C.
embed:
	uv run python -m collect embed

stats:
	@uv run python -m collect stats

bench:
	uv run python -m collect bench

# Snapshot the catalog and install it on the Fly app as /data/catalog.db
push:
	uv run python -m collect push

test:
	uv run pytest -q

# Install fetch, embed and a daily push as systemd services for the current user (needs sudo)
services:
	@for f in systemd/*.service systemd/*.timer; do \
	  sed -e "s|@USER@|$$(id -un)|g" -e "s|@DIR@|$$(pwd)|g" -e "s|@UV@|$(UV)|g" -e "s|@HOME@|$$HOME|g" $$f \
	    | sudo tee /etc/systemd/system/$$(basename $$f) >/dev/null; \
	  echo "installed /etc/systemd/system/$$(basename $$f)"; \
	done
	sudo systemctl daemon-reload
	@echo "start with: sudo systemctl enable --now taste-fetch taste-embed taste-push.timer"
