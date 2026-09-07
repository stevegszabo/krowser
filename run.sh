#!/usr/bin/env bash
set -euo pipefail
exec .venv/bin/uvicorn krowser.main:app --reload --host 0.0.0.0 --port 8000
