# Web console

`http://<server-ip>:8099` - credentials come from `.env` (`CONSOLE_USER`, `CONSOLE_PASSWORD`).
The installer prints them at the end of the run.

## What it can do

| page | actions |
|---|---|
| dashboard | container states, restart/start/stop for gateway, panel and sing-box, "render + restart gateway", "apply forwarding", "route the panel through the gateway" |
| outbounds | paste one link per line, optional tag, group, DNS override, chain (`via`), enable/disable, delete |
| forwarding | add port / port-range / whole-IP forwards, tune Hysteria2 port hopping, apply to nftables |

Every mutation is applied immediately: configs are re-rendered, the gateway is restarted and
nftables is reloaded. The dashboard also shows the timestamp of the last action.

## Adding outbounds

Paste any mix of `vless://`, `vmess://`, `trojan://`, `ss://`, `socks://`, `http://`,
`hysteria2://`, `tuic://`, one per line, then press **افزودن و اعمال**.

- The whole batch is validated first: if one line is broken, nothing is added and the error names
  the offending line.
- With a single line you can set the tag. With several, tags come from each link's fragment name
  (`#CDN2`) or its host, and collisions get `-2`, `-3` suffixes.
- `IP جایگزین DNS` writes a `dns.hosts` entry - use it when the CDN hostname is poisoned.
- `از طریق` chains the new outbound behind an existing one (`proxySettings.tag` in Xray).

## Adding forwards

| fields | result |
|---|---|
| `tcp`, port `3389`, target `10.0.0.5:3389` | one port to one host, port rewritten |
| `both`, `50000-50100`, target `172.28.60.12` | a whole range, port preserved |
| `tcp`, `*`, listen IP `203.0.113.5`, target `10.0.0.9` | every port of one public IP to another host, ports preserved |
| `udp`, `443`, listen IP `...`, target `172.28.60.12:8443` | expose sing-box on another address |

Rules are stored in `config/forwarding.json`, rendered into `config/nftables/outlander.nft` and
applied with `nft -f`. Targets inside `172.28.60.0/24` automatically get a `DOCKER-USER` accept.

The **Hysteria2 port hopping** card edits the `hop` block: a UDP range (default `20000-40000`)
DNAT'd onto the sing-box listener.

## State and files

```
data/console/state.json     last action per area (shown on the dashboard)
data/console-password.txt   generated password when CONSOLE_PASSWORD is unset
```

Files the console writes are chowned to `HOST_UID:HOST_GID` (set by the installer), so the CLI
still works as your normal user.

## Security

The console can restart containers and rewrite the host firewall - treat it as root:

1. Set a long `CONSOLE_PASSWORD` (the installer generates one) and change it by editing `.env`
   then `docker compose up -d console`.
2. Do not expose port 8099 to the internet. Use `ssh -L 8099:127.0.0.1:8099 root@server`, or
   restrict it in `ufw`/`nftables` to your own IPs.
3. Sessions live in memory, expire after 12h (`CONSOLE_SESSION_TTL`), and are dropped when the
   container restarts. Login is rate limited to 8 failures per IP per 10 minutes.
4. CSRF tokens are required on every mutating form; cookies are `HttpOnly` + `SameSite=Lax`.
5. If you front it with a reverse proxy, keep the `/healthz` path open and add TLS.

## Running it outside Docker (for development)

```bash
OUTLANDER_ROOT=$PWD CONSOLE_PASSWORD=dev CONSOLE_PORT=8099 \
  python3 -m console
```

Without a docker socket the container controls report `unknown` and restart actions return an
error; the inventory and forwarding editors keep working.
