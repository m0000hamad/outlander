#!/usr/bin/env bash
# Apply the rendered nftables DNAT rules (port hopping + custom forwards).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

if [[ ! -f config/nftables/outlander.nft ]]; then
  python3 -m outlander forward render
fi

nft -f config/nftables/outlander.nft
echo "nftables rules loaded"

if [[ -f config/nftables/docker-allow.sh ]]; then
  bash config/nftables/docker-allow.sh || echo "warning: could not add DOCKER-USER accepts"
fi

nft list table ip outlander
