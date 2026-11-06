#!/usr/bin/env bash
# Runs the "needs you" action rule tests (see actions_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/actions_test.py"
