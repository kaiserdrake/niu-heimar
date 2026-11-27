#!/usr/bin/env bash
# Runs the niu-forward satellite forwarder tests (see forward_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/forward_test.py"
