#!/usr/bin/env bash
# Runs the Claude plan-limit tests (see limits_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/limits_test.py"
