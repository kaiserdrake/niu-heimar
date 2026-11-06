#!/usr/bin/env bash
# Runs the niu-local digest tools tests (logs / ci / git). No network, no docker, no real models.
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/runs_test.py"
