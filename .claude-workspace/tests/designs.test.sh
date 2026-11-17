#!/usr/bin/env bash
# Runs the design doc tests (see designs_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/designs_test.py"
