#!/usr/bin/env bash
# Runs the hub-side ingest validator/storage tests (see ingest_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/ingest_test.py"
