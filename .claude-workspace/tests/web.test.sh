#!/usr/bin/env bash
# Runs the niu-dash end-to-end tests (see web_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/web_test.py"
