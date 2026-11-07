#!/usr/bin/env bash
# Runs the Research tab backend tests (see research_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/research_test.py"
