#!/usr/bin/env bash
# Runs the transcript token scanner and Token usage tests (see tokens_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/tokens_test.py"
