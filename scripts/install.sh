#!/usr/bin/env bash
# One shot Ubuntu 22.04/24.04 bootstrap for Outlander.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUTBOUND=""
PANEL_PORT=""
HY2_DOMAIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --outbound) OUTBOUND="$2"; shift 2 ;;
    --panel-port) PANEL_PORT="$2"; shift 2 ;;
    --hy2-domain) HY2_DOMAIN="$2"; shift 2 ;;
    -h|--help)
      echo "usage: sudo bash scripts/install.sh [--outbound 'vless://...'] [--panel-port 2053] [--hy2-domain hy2.example.com]"
      exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "run this script as root (sudo bash scripts/install.sh ...)" >&2
  exit 1
fi

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

say "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl jq nftables sqlite3 python3 openssl ufw unzip make cron >/dev/null

if ! command -v docker >/dev/null 2>&1; then
  say "installing docker"
  # Use Ubuntu packages: get.docker.com may return HTTP 403 on some hosts.
  apt-get install -y docker.io docker-compose-v2
fi
if ! docker compose version >/dev/null 2>&1; then
  say "installing Docker Compose v2"
  if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'install ok installed'; then
    apt-get install -y docker-compose-plugin
  else
    apt-get install -y docker-compose-v2
  fi
fi
docker compose version >/dev/null
systemctl enable --now docker
# Fail before generating configuration if the daemon cannot start.
docker info >/dev/null

say "kernel tuning (bbr, fq, tcp_fastopen)"
cat > /etc/sysctl.d/99-outlander.conf <<'EOF'
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.ip_forward = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_slow_start_after_idle = 0
net.core.somaxconn = 65535
fs.file-max = 2097152
EOF
sysctl --system >/dev/null

if [[ ! -f .env ]]; then
  say "creating .env"
  cp .env.example .env
  PANEL_PASS="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20)"
  HY2_PASS="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)"
  OBFS_PASS="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)"
  sed -i "s|^PANEL_PASSWORD=.*|PANEL_PASSWORD=${PANEL_PASS}|" .env
  sed -i "s|^HY2_PASSWORD=.*|HY2_PASSWORD=${HY2_PASS}|" .env
  sed -i "s|^HY2_OBFS_PASSWORD=.*|HY2_OBFS_PASSWORD=${OBFS_PASS}|" .env
  sed -i "s|^CONSOLE_PASSWORD=.*|CONSOLE_PASSWORD=${PANEL_PASS}|" .env
  sed -i "s|^HOST_UID=.*|HOST_UID=${SUDO_UID:-0}|" .env
  sed -i "s|^HOST_GID=.*|HOST_GID=${SUDO_GID:-0}|" .env
  if [[ -n "$PANEL_PORT" ]]; then sed -i "s|^PANEL_PORT=.*|PANEL_PORT=${PANEL_PORT}|" .env; fi
  if [[ -n "$HY2_DOMAIN" ]]; then sed -i "s|^HY2_SNI=.*|HY2_SNI=${HY2_DOMAIN}|" .env; fi
  if [[ -z "${HY2_DOMAIN:-}" ]]; then sed -i "s|^HY2_ENABLED=.*|HY2_ENABLED=0|" .env; fi
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

mkdir -p data/x-ui data/acme certs

say "preparing inventory"
python3 -m outlander init >/dev/null
if [[ -n "$OUTBOUND" ]]; then
  python3 -m outlander add "$OUTBOUND"
fi
python3 -m outlander render
python3 -m outlander forward render

if [[ "${HY2_ENABLED:-0}" == "1" ]]; then
  if [[ ! -f certs/fullchain.pem ]]; then
    say "hysteria2 is enabled but certs/fullchain.pem is missing"
    echo "    run: sudo bash scripts/cert.sh ${HY2_SNI} && docker compose --profile extra restart singbox"
  fi
fi

say "starting the stack"
docker compose up -d --build

say "waiting for the panel database"
for _ in $(seq 1 60); do
  [[ -f data/x-ui/x-ui.db ]] && break
  sleep 2
done

if [[ -f data/x-ui/x-ui.db ]]; then
  say "setting panel credentials"
  PANEL_CONFIGURED=0
  for binary in /app/x-ui /usr/local/x-ui/x-ui; do
    if docker exec ol-panel test -x "$binary" 2>/dev/null; then
      docker exec ol-panel "$binary" setting -username "$PANEL_USERNAME" -password "$PANEL_PASSWORD" -port "$PANEL_PORT"
      PANEL_CONFIGURED=1
      break
    fi
  done
  if [[ "$PANEL_CONFIGURED" != 1 ]]; then
    echo "panel executable not found; inspect: docker logs --tail 80 ol-panel" >&2
    exit 1
  fi
  # Settings are persisted to SQLite; restart to load the configured port.
  docker restart ol-panel >/dev/null
  say "waiting for the panel HTTP listener"
  PANEL_READY=0
  for _ in $(seq 1 60); do
    if curl --noproxy '*' -sS -o /dev/null --connect-timeout 1 --max-time 2 "http://127.0.0.1:${PANEL_PORT}/" 2>/dev/null; then
      PANEL_READY=1
      break
    fi
    sleep 2
  done
  if [[ "$PANEL_READY" != 1 ]]; then
    echo "panel did not start on port ${PANEL_PORT}; inspect: docker logs --tail 80 ol-panel" >&2
    exit 1
  fi
  say "routing panel traffic through the gateway"
  python3 -m outlander panel patch || echo "    warning: panel patch failed, see docs/PANEL.md"
else
  echo "panel database was not created; inspect: docker logs --tail 80 ol-panel" >&2
  exit 1
fi

if [[ -f config/nftables/outlander.nft ]]; then
  say "applying port forwarding rules"
  nft -f config/nftables/outlander.nft || echo "    warning: could not apply nftables rules"
  bash config/nftables/docker-allow.sh || true
fi

say "installing the forwarding systemd unit (rules survive reboot)"
cat > /etc/systemd/system/outlander-forward.service <<EOF
[Unit]
Description=Outlander nftables forwarding rules
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f ${ROOT}/config/nftables/outlander.nft
ExecStartPost=/bin/bash ${ROOT}/config/nftables/docker-allow.sh

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now outlander-forward.service >/dev/null 2>&1 || true

say "installing the certificate renewal cron"
CRON_RENEW="15 4 * * * cd ${ROOT} && bash scripts/cert.sh --renew >> /var/log/outlander-cert.log 2>&1"
( (crontab -l 2>/dev/null || true) | sed '\|scripts/cert.sh --renew|d'; echo "$CRON_RENEW" ) | crontab -

if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null 2>&1 || true
  ufw allow "${PANEL_PORT:-2053}/tcp" >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw allow 8080/tcp >/dev/null 2>&1 || true
  ufw allow 8443/tcp >/dev/null 2>&1 || true
  ufw allow 2087/tcp >/dev/null 2>&1 || true
  ufw allow 8388/tcp >/dev/null 2>&1 || true
  ufw allow 8388/udp >/dev/null 2>&1 || true
  ufw allow "${HY2_PORT:-8443}/udp" >/dev/null 2>&1 || true
  ufw allow "${CONSOLE_PORT:-8099}/tcp" >/dev/null 2>&1 || true
  ufw allow 20000:40000/udp >/dev/null 2>&1 || true
fi

IP="$(curl -fsS4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
PANEL_PATH="$(grep -E '^PANEL_BASE_PATH=' .env 2>/dev/null | cut -d= -f2 || true)"
cat <<EOF

────────────────────────────────────────────────────────────
 Outlander is up.

 Console   : http://${IP}:${CONSOLE_PORT:-8099}
 User      : ${CONSOLE_USER:-admin}
 Password  : ${CONSOLE_PASSWORD}

 Panel     : http://${IP}:${PANEL_PORT:-2053}/${PANEL_PATH}
 Username  : ${PANEL_USERNAME}
 Password  : ${PANEL_PASSWORD}

 Gateway   : 127.0.0.1:${GATEWAY_SOCKS_PORT:-10808} (socks5, chained)
 Channels  : $(python3 -m outlander list 2>/dev/null | tail -n +2 | wc -l)

 Next:
   1. open the console, paste your CDN outbound link(s), press "افزودن و اعمال"
   2. add forwarding rules from the console (or `outlander forward add ...`)
   3. open the panel and create client inbounds (docs/PANEL.md)
   4. checks: make doctor
────────────────────────────────────────────────────────────
EOF
