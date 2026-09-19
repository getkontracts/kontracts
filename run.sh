#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ ! -f .venv/bin/activate ] || . .venv/bin/activate
[ -f .env ] || cp .env.example .env
printf '\nOpen http://localhost:8000. Press Ctrl+C to stop.\n\n'
exec python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --no-access-log
