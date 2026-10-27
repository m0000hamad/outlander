# Outlander

Chained, multi-protocol proxy infrastructure for a single Ubuntu box.

- **Console**: a small bilingual (Persian/English) web UI (:8099) to paste outbound links, add port/IP forwards and apply them instantly.
- **Panel**: 3x-ui (the Sanaei panel) on host networking, so REALITY and per-client stats see real client IPs.
- **Gateway**: a separate Xray instance that owns *egress*. It turns any share link (`vless://`, `vmess://`, `trojan://`, `ss://`, `hysteria2://`, `tuic://`) into a health-checked upstream, balances across all of them with `leastPing`, and falls back to direct.
- **Extra protocols**: Hysteria2 (salamander obfs + port hopping) and TUIC via sing-box, bridged into the gateway over authenticated SOCKS5.
- **Forwarding**: nftables DNAT for ports/IPs and a UDP range for port hopping, plus an optional HAProxy SNI router.

Everything is generated from `config/inventory.json` by a zero-dependency Python CLI, so no upstream credentials ever land in the repository.

Persian guide: [README.fa.md](README.fa.md)

## Install

```bash
git clone https://github.com/m0000hamad/outlander.git outlander && cd outlander
sudo bash scripts/install.sh
make doctor
```

Then open the console at `http://<server>:8099`, log in with the printed credentials and paste your
CDN link(s) into **outbounds**.

The installer provisions Docker, BBR/fq, the `.env` secrets, renders configs, starts the stack, sets panel credentials and wires the panel's outbound to the gateway.

## Daily commands

```bash
python3 -m outlander add '<link>' --tag cdn3 [--group cdn] [--resolve 1.2.3.4] [--via cdn2]
python3 -m outlander list
python3 -m outlander rm cdn3
python3 -m outlander render && docker compose restart gateway
python3 -m outlander forward add --proto tcp --listen 3389 --target 10.0.0.5:3389
python3 -m outlander forward render
make test | make doctor | make backup | make panel
```

## Ports

| Purpose | Port |
|---|---|
| Console | `CONSOLE_PORT` (8099) |
| Panel | `PANEL_PORT` (2053) |
| REALITY | 443/tcp |
| VLESS+WS behind CDN | 8080/tcp |
| VLESS+WS+TLS | 8443/tcp |
| Trojan | 2087/tcp |
| Shadowsocks-2022 | 8388/tcp+udp |
| Hysteria2 | 8443/udp (+ 20000-40000/udp hop) |
| TUIC | 8444/udp |
| Gateway SOCKS (localhost only) | 10808 |

## Removing

```bash
sudo bash scripts/uninstall.sh                # containers, network, systemd unit, firewall, cron
sudo bash scripts/uninstall.sh --purge-data   # + secrets, panel database, certificates, backups
sudo bash scripts/uninstall.sh --purge-data --remove-dir --yes   # delete the checkout too
```

Runtime state is always removed; `.env`, `config/`, `data/`, `certs/` and `backups/` are kept
unless you pass `--purge-data`. Docker itself is never touched.

## Docs

- [docs/CONSOLE.md](docs/CONSOLE.md) - the web UI
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - how the pieces fit and why
- [docs/PANEL.md](docs/PANEL.md) - creating inbounds in 3x-ui
- [docs/CDN.md](docs/CDN.md) - CDN fronting, origin lockdown, parameters
- [docs/PROTOCOLS.md](docs/PROTOCOLS.md) - which protocol to use when
- [docs/FORWARDING.md](docs/FORWARDING.md) - ports, IPs, SNI routing
- [docs/OPERATIONS.md](docs/OPERATIONS.md) - day-2 operations

Use it in line with local law and your provider's terms.
