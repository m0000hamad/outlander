#!/usr/bin/env bash
# Lock the origin down to CDN edge ranges so the real IP never answers directly.
# Only the plain-HTTP edge ports (80/8080/8443) are filtered, hysteria2 stays open.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

ADMIN_IPS="${ADMIN_IPS:-}"
EDGE_PORTS="${EDGE_PORTS:-80, 8080, 8443}"

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

say "fetching Cloudflare edge ranges"
CF4="$(curl -fsS --max-time 10 https://www.cloudflare.com/ips-v4)" || {
  echo "could not fetch Cloudflare ranges - aborting so we do not lock you out" >&2
  exit 1
}

OUT=config/nftables/cdn.nft
mkdir -p config/nftables
{
  echo "table ip outlander_cdn"
  echo "delete table ip outlander_cdn"
  echo "table ip outlander_cdn {"
  echo "  set cf4 {"
  echo "    type ipv4_addr"
  echo "    flags interval"
  echo "    auto-merge"
  echo "    elements = {"
  while IFS= read -r range; do
    [[ -n "$range" ]] && echo "      $range,"
  done <<< "$CF4"
  echo "    }"
  echo "  }"
  echo "  chain input {"
  echo "    type filter hook input priority -10; policy accept;"
  echo "    iif lo accept"
  echo "    ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } accept"
  if [[ -n "$ADMIN_IPS" ]]; then
    echo "    ip saddr { $ADMIN_IPS } accept"
  fi
  echo "    tcp dport { $EDGE_PORTS } ip saddr != @cf4 drop"
  echo "  }"
  echo "}"
} > "$OUT"

nft -f "$OUT"
say "origin edge ports ($EDGE_PORTS) now accept Cloudflare ranges only"
nft list table ip outlander_cdn | head -n 20
