#!/usr/bin/env bash
# Runs the multi-machine end-to-end test: a real niu-dash hub plus a satellite forwarder (see e2e_multi_test.py).
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/e2e_multi_test.py"
