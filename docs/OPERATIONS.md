# Operations

## Daily

```bash
make ps                     # what is running
make logs                   # follow all logs
docker compose logs -f gateway
python3 -m outlander list   # active upstreams
make doctor                 # host + config sanity
```

## Updating

```bash
git pull
python3 -m outlander render
docker compose pull
docker compose up -d
make panel                  # re-apply the gateway outbound after a panel upgrade
```

Pinned behaviour: the images track `latest`, so pin them in `docker-compose.yml`
(`ghcr.io/xtls/xray-core:25.x.y`, `ghcr.io/mhsanaei/3x-ui:2.x.y`) once a combination is known good.

## Backups

```bash
make backup        # backups/outlander-<stamp>.tar.gz: .env, config, panel db, certs
```

Cron:

```
15 4 * * * cd /opt/outlander && bash scripts/backup.sh >> /var/log/outlander-backup.log 2>&1
```

Restore: stop the stack, untar over the repo, `docker compose up -d`.

## Certificates

```bash
sudo bash scripts/cert.sh hy2.example.com     # issues, installs, restarts sing-box, adds cron
```

Renewal is `certbot --keep-until-expiring` daily at 03:00. It needs TCP/80 for a few seconds; if the
panel occupies 80, use the DNS-01 command printed on failure.

## Monitoring

- Gateway health: `docker inspect --format '{{.State.Health.Status}}' ol-gateway` (`xray -test`
  every 60s).
- Per-channel latency: `docker compose logs gateway | grep -i observatory`.
- Panel traffic stats: per-client counters in the panel UI.
- Real end-to-end check from outside:

```bash
curl -x socks5h://<server-ip>:10808 -sS -m 10 https://ifconfig.me   # only if you exposed 10808
curl -sS -m 10 https://<cdn-host>/                                     # CDN edge reachable
```

The gateway SOCKS port is published on `127.0.0.1` only by default; uncomment the port mapping in
`docker-compose.yml` if you want to test it remotely, and firewall it.

## Capacity

- `ulimits.nofile` is set to 1M for the proxy containers; the host `fs.file-max` is raised by
  `install.sh`.
- BBR + fq are applied system-wide.
- Watch `docker stats` and `ss -s`; Xray is single-process but multithreaded through Go's scheduler.
- Hysteria2 is the most CPU-hungry per Mbps; SS-2022 is the cheapest.

## Recovering

| situation | action |
|---|---|
| bad config, gateway crash-loops | `git checkout config/inventory.json` (or fix the channel), `render`, restart |
| panel DB lost | restore `data/x-ui` from backup; clients are recreated from the panel |
| CDN blocked | add another channel with `--group cdn`, the balancer picks it up after restart |
| server IP burned | new server: restore the repo + backup, point the CDN record at the new IP |
| all upstreams dead | gateway falls back to `direct`; clients still work if the server itself has free access |

## Security checklist

- Panel on a high random port, web base path set, 2FA on.
- `PANEL_PASSWORD` rotated; `.env` is `600` and gitignored.
- Origin ports locked to CDN ranges (`scripts/cdn-firewall.sh`).
- SSH key-only, root login disabled, `ufw` enabled.
- Upstream links are secrets: keep `config/inventory.json` out of git, rotate UUIDs when a device is
  lost.
