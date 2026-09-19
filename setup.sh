#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/update_zipcodes.py
[ -f .env ] || cp .env.example .env
exec sh ./run.sh
