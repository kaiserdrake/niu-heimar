#!/usr/bin/env bash
# Runs the Topics panel backend tests (see topics_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/topics_test.py"
