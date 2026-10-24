#!/usr/bin/env bash
# Issue (or renew) the certificate Hysteria2/TUIC use, then reload sing-box.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DOMAIN="${1:-}"
EMAIL="${EMAIL:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "usage: sudo bash scripts/cert.sh hy2.example.com" >&2
  echo "   or: EMAIL=you@example.com sudo bash scripts/cert.sh hy2.example.com" >&2
  exit 1
fi
if [[ -z "$EMAIL" ]]; then
  EMAIL="admin@${DOMAIN#*.}"
fi

mkdir -p data/letsencrypt certs

if ! docker image inspect certbot/certbot:latest >/dev/null 2>&1; then
  docker pull certbot/certbot:latest >/dev/null
fi

# Standalone mode needs TCP/80 free for a few seconds. Stop nothing else.
set +e
docker run --rm \
  -p 80:80 \
  -v "$ROOT/data/letsencrypt:/etc/letsencrypt" \
  -v "$ROOT/data/letsencrypt-lib:/var/lib/letsencrypt" \
  certbot/certbot:latest certonly --standalone \
  --non-interactive --agree-tos --keep-until-expiring \
  --email "$EMAIL" -d "$DOMAIN"
STATUS=$?
set -e

if [[ $STATUS -ne 0 ]]; then
  echo "certbot failed; if port 80 is busy use DNS-01 instead:" >&2
  echo "  docker run --rm -it -v \$PWD/data/letsencrypt:/etc/letsencrypt certbot/certbot certonly --manual --preferred-challenges dns -d $DOMAIN" >&2
  exit $STATUS
fi

install -m 644 "data/letsencrypt/live/$DOMAIN/fullchain.pem" certs/fullchain.pem
install -m 600 "data/letsencrypt/live/$DOMAIN/privkey.pem" certs/privkey.pem
chmod 644 certs/fullchain.pem certs/privkey.pem

echo "certificate installed for $DOMAIN"
if docker ps --format '{{.Names}}' | grep -qx ol-singbox; then
  docker restart ol-singbox >/dev/null
  echo "sing-box restarted"
fi

CRON_LINE="0 3 * * * cd $ROOT && EMAIL=$EMAIL bash scripts/cert.sh $DOMAIN >/var/log/outlander-cert.log 2>&1"
if ! (crontab -l 2>/dev/null || true) | grep -Fq "scripts/cert.sh $DOMAIN"; then
  ( (crontab -l 2>/dev/null || true); echo "$CRON_LINE" ) | crontab -
  echo "renewal cron installed"
fi
