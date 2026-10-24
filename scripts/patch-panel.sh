#!/usr/bin/env bash
# Push the gateway outbound + catch-all rule into the 3x-ui xray template.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f data/x-ui/x-ui.db ]]; then
  echo "data/x-ui/x-ui.db not found - start the panel once (docker compose up -d)" >&2
  exit 1
fi

ARGS=("$@")
if [[ ${EUID} -ne 0 ]]; then
  echo "needs root to read the panel database; re-running with sudo"
  exec sudo -E python3 -m outlander panel patch "${ARGS[@]}"
fi

exec python3 -m outlander panel patch "${ARGS[@]}"
