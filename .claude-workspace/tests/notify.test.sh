#!/usr/bin/env bash
# Runs the niu-notify Discord notifier tests (see notify_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/notify_test.py"
