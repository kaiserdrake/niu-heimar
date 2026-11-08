#!/usr/bin/env bash
# Runs the Claude usage-history tests (see usage_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/usage_test.py"
