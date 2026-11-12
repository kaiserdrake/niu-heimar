#!/usr/bin/env bash
# Runs the agent catalog tests (see catalog_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/catalog_test.py"
