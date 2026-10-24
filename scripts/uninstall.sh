#!/usr/bin/env bash
# Remove Outlander from a host: containers, network, systemd unit, firewall rules, cron.
#
#   sudo bash scripts/uninstall.sh                  # runtime only, keeps data/secrets
#   sudo bash scripts/uninstall.sh --purge-data     # + .env, inventory, panel db, certs, backups
#   sudo bash scripts/uninstall.sh --purge-data --remove-dir --yes   # delete the checkout too
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ASSUME_YES=0
PURGE_DATA=0
PURGE_IMAGES=0
REMOVE_DIR=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    --purge-data) PURGE_DATA=1; shift ;;
    --purge-images) PURGE_IMAGES=1; shift ;;
    --remove-dir) REMOVE_DIR=1; shift ;;
    -h|--help)
      cat <<'EOF'
usage: sudo bash scripts/uninstall.sh [options]

  -y, --yes        do not ask for confirmation
  --purge-data     also delete .env, config/inventory.json, config/forwarding.json,
                   rendered configs, data/ (panel database), certs/ and backups/
  --purge-images   also delete the locally built outlander-console image
  --remove-dir     delete this checkout directory at the end (implies --purge-data is separate)

What is always removed:
  ol-gateway / ol-panel / ol-console / ol-singbox / ol-haproxy containers,
  the ol-core docker network, outlander-forward.service,
  the "outlander" and "outlander_cdn" nftables tables, the DOCKER-USER accepts
  added for forwarding, the ufw rules added by install.sh and the certbot cron entry.

Docker itself is never removed.
EOF
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "run this script as root (sudo bash scripts/uninstall.sh ...)" >&2
  exit 1
fi

if [[ -f .env ]]; then
  set +u
  # shellcheck disable=SC1091
  source .env
  set -u
fi

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }

if [[ $ASSUME_YES -ne 1 ]]; then
  cat <<EOF
این کار این موارد را حذف می‌کند:
  - کانتینرهای ol-gateway / ol-panel / ol-console / ol-singbox / ol-haproxy و شبکهٔ ol-core
  - یونیت outlander-forward.service
  - جدول‌های nftables (outlander, outlander_cdn) و قوانین DOCKER-USER مربوط به فوروارد
  - قوانین ufw که نصب‌کننده اضافه کرده و کران certbot
  - داده‌ها/رمزها/سرتیفیکیت‌ها: $( [[ $PURGE_DATA -eq 1 ]] && echo "بله (--purge-data)" || echo "نه (پیش‌فرض حفظ می‌شود)" )
  - پوشهٔ پروژه: $( [[ $REMOVE_DIR -eq 1 ]] && echo "بله (--remove-dir)" || echo "نه (پیش‌فرض حفظ می‌شود)" )

برای ادامه «yes» بنویسید:
EOF
  read -r answer
  [[ "$answer" == "yes" ]] || { echo "aborted"; exit 1; }
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    return 1
  fi
}

# --- containers, network, image ------------------------------------------
if command -v docker >/dev/null 2>&1; then
  say "stopping and removing containers"
  compose down --remove-orphans >/dev/null 2>&1 || true
  for name in ol-gateway ol-panel ol-console ol-singbox ol-haproxy; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done
  docker network rm ol-core >/dev/null 2>&1 || true
  if [[ $PURGE_IMAGES -eq 1 ]]; then
    docker image rm outlander-console:latest >/dev/null 2>&1 || true
    say "removed the outlander-console image"
  fi
else
  warn "docker not found, skipping containers"
fi

# --- systemd unit --------------------------------------------------------
if [[ -f /etc/systemd/system/outlander-forward.service ]]; then
  say "removing outlander-forward.service"
  systemctl disable --now outlander-forward.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/outlander-forward.service
  systemctl daemon-reload >/dev/null 2>&1 || true
fi
rm -f /etc/systemd/system/outlander-forward.service.d/override.conf >/dev/null 2>&1 || true

# --- nftables ------------------------------------------------------------
if command -v nft >/dev/null 2>&1; then
  say "flushing nftables tables"
  for table in outlander outlander_cdn; do
    nft delete table ip "$table" >/dev/null 2>&1 || true
  done
fi

# --- DOCKER-USER accepts added for forwarding ----------------------------
if command -v iptables >/dev/null 2>&1; then
  say "cleaning DOCKER-USER rules"
  for _ in $(seq 1 50); do
    rule="$(iptables -S DOCKER-USER 2>/dev/null | grep -- '172.28.60.' | head -n1 || true)"
    [[ -z "$rule" ]] && break
    read -r -a parts <<< "${rule#-A }"
    iptables -D "${parts[@]}" >/dev/null 2>&1 || break
  done
fi

# --- ufw rules -----------------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  say "removing ufw rules added by install.sh"
  for rule in \
    "443/tcp" "8080/tcp" "8443/tcp" "2087/tcp" "8388/tcp" "8388/udp" \
    "${PANEL_PORT:-2053}/tcp" "${CONSOLE_PORT:-8099}/tcp" \
    "${HY2_PORT:-8443}/udp" "20000:40000/udp"; do
    ufw --force delete allow "$rule" >/dev/null 2>&1 || true
  done
fi

# --- certbot renewal cron ------------------------------------------------
if command -v crontab >/dev/null 2>&1; then
  current="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$current" | grep -q 'scripts/cert.sh'; then
    say "removing certbot renewal cron entry"
    printf '%s\n' "$current" | grep -v 'scripts/cert.sh' | crontab - || true
  fi
fi

# --- data and secrets ----------------------------------------------------
if [[ $PURGE_DATA -eq 1 ]]; then
  say "deleting data, secrets and certificates"
  rm -rf data certs backups
  rm -f .env \
    config/inventory.json \
    config/forwarding.json \
    config/xray/config.json \
    config/sing-box/config.json \
    config/nftables/outlander.nft \
    config/nftables/docker-allow.sh \
    config/nftables/cdn.nft
else
  warn "kept: .env, config/inventory.json, config/forwarding.json, data/, certs/, backups/"
  warn "re-run with --purge-data to delete them"
fi

# --- the checkout itself -------------------------------------------------
if [[ $REMOVE_DIR -eq 1 ]]; then
  case "$ROOT" in
    /|/root|/home|/usr|/etc|"$HOME") echo "refusing to delete $ROOT" >&2; exit 1 ;;
  esac
  say "deleting $ROOT"
  cd /
  rm -rf "$ROOT"
  echo "Outlander removed."
  exit 0
fi

cat <<EOF

────────────────────────────────────────────────────────────
 Runtime removed. Leftovers (if any):

   repo    : $ROOT
   command : rm -rf '$ROOT'
   docker  : docker compose down -v --rmi local   (already done)

 Full wipe, no questions:
   sudo bash scripts/uninstall.sh --purge-data --remove-dir --yes
────────────────────────────────────────────────────────────
EOF
