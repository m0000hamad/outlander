#!/usr/bin/env bash
# Archive everything worth restoring: env, inventory, generated configs, panel db, certs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

mkdir -p backups
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="backups/outlander-${STAMP}.tar.gz"

tar -czf "$TARGET" \
  --warning=no-file-changed \
  .env \
  config \
  data/x-ui \
  certs \
  2>/dev/null || true

chmod 600 "$TARGET"
echo "backup written: $TARGET ($(du -h "$TARGET" | cut -f1))"

find backups -name 'outlander-*.tar.gz' -mtime +14 -delete
echo "pruned backups older than 14 days"
