#!/usr/bin/env bash
# Runs the LiveState cache tests (see live_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/live_test.py"
