#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/uvicorn krowser.main:app --reload --host 0.0.0.0 --port 8000
