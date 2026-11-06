#!/usr/bin/env bash
# Runs the local Ollama agent tests (see ollama_test.py). No network, no real models.
set -uo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/ollama_test.py"
