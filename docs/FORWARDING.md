# Forwarding ports, IPs and SNI

Three layers, each solving a different problem:

| need | tool | file |
|---|---|---|
| Hysteria2 port hopping (UDP range) | nftables DNAT | `config/nftables/outlander.nft` |
| forward a host port to another machine or container | nftables DNAT | same |
| pick a backend by domain name before TLS terminates | HAProxy | `config/haproxy/haproxy.cfg` |
| keep the origin IP out of DNS | CDN | see `docs/CDN.md` |

## Port hopping

`config/forwarding.json`:

```json
{
  "hop": {"enabled": true, "proto": "udp", "range": "20000-40000", "target": "172.28.60.12:8443"},
  "rules": []
}
```

```bash
python3 -m outlander forward render
sudo bash scripts/forward-apply.sh
```

The rule is `udp dport 20000-40000 dnat ip to 172.28.60.12:8443`, evaluated before Docker's own
DNAT chain (`priority dstnat - 10`), and the apply script inserts a matching `DOCKER-USER` accept.
Clients then connect to a random port in the range (`mport=20000-40000`) on every handshake, so a
single blocked port does not kill the session.

Keep the range wide enough to be useful (20k ports) but inside the ephemeral-safe space. Ask your
hosting provider first - some filter UDP in `20000+`.

## Plain L4 forwarding

```bash
python3 -m outlander forward add --proto tcp  --listen 3389     --target 10.0.0.5:3389
python3 -m outlander forward add --proto both --listen 50000-50100 --target 172.28.60.12
python3 -m outlander forward add --proto tcp  --listen '*' --listen-ip 203.0.113.5 --target 10.0.0.9
python3 -m outlander forward render
sudo bash scripts/forward-apply.sh
python3 -m outlander forward list
```

The `*` form forwards **every port** that arrives on `listen_ip` to the target host, preserving port
numbers (`ip daddr 203.0.113.5 meta l4proto tcp dnat ip to 10.0.0.9`). It needs both `listen_ip` and
a target without a port, because the port is whatever the client asked for.

Notes:

- A target without a port preserves the original destination port - that is how ranges map.
- Targets inside `172.28.60.0/24` (the compose network) automatically get a `DOCKER-USER` accept.
- Return traffic is handled by `ct status dnat masquerade`, so forwarded clients see the server IP,
  not the original one.
- `forward render` rewrites the whole nftables table from the JSON; hand edits are lost.

Removing a rule:

```bash
# edit config/forwarding.json (remove the entry) then:
python3 -m outlander forward render && sudo bash scripts/forward-apply.sh
```

## Modes: direct, tunnel, auto

Every rule carries a `mode`:

| mode | what runs | when to use |
|---|---|---|
| `nft` | nftables DNAT (default) | fastest, kernel level, any TCP/UDP, no protocol involved |
| `dokodemo` | Xray `dokodemo-door` inbound in the panel | the target is not reachable from the server directly; traffic leaves through the gateway and the CDN chain |
| `auto` | whichever probe succeeds | let the console decide and keep deciding |

`auto` probes the target twice: a direct TCP connection from the host, then a SOCKS5 `CONNECT`
through the gateway (`127.0.0.1:10808`). Direct wins (`nft`), otherwise the tunnel is used
(`dokodemo`). A worker re-checks every `AUTO_INTERVAL` seconds (default 300) and switches the rule
when the path changes; `AUTO_FORWARDS=0` disables it. The rules table shows the current path and the
last error.

Dokodemo rules are never written to nftables: the panel listens on the port and its catch-all
routing rule sends the connection to `ol-gateway`, which balances over the upstream channels and
fails over between them. That is how a blocked destination is reached from the CDN exit. Deleting
such a rule also deletes its inbound.

Limits: only a single port can be dokodemo (ranges and `*` always resolve to `nft`), and `listen_ip`
is nft-only. UDP over a WS-based upstream depends on that transport's UDP support, so prefer TCP for
tunnel mode.

## Forwarding to a public host

The target may be an IP or a hostname (`s3.example.com:2083`). At render time the hostname is
resolved to IPv4 addresses and the first one that accepts a TCP connection on the target port is
pinned, because nftables cannot write a rule for a name and CDN anycast addresses are not all
reachable from every host. If the target moves, re-render:
`outlander forward render && make forward-apply`.

The CLI and the console refuse a rule whose listen port already has a listener on the host (443, 2053,
8080, ...). DNAT is evaluated before the local service, so such a forward silently hijacks it - that

is what breaks a site running behind Caddy or a docker-proxy port. Use `--listen-ip` to forward only a
specific address, or pick a free port.

Docker sets the `FORWARD` policy to `DROP`, so a DNAT'd packet heading for an *external* host is
silently dropped unless something accepts it. The generated `config/nftables/docker-allow.sh`
therefore always starts with:

```bash
iptables -C DOCKER-USER -m conntrack --ctstate DNAT -j ACCEPT || \
  iptables -I DOCKER-USER 1 -m conntrack --ctstate DNAT -j ACCEPT
```

`forward-apply.sh`, the console and `outlander-forward.service` all run that script, so forwards to
public hosts work after a reboot too. If you apply the nft rules by hand, run the script as well.

### A forward is a raw relay, not a proxy

`2083 -> s3.example.com:2083` copies TCP/UDP bytes. It does not terminate TLS and it is not a proxy
endpoint:

* the client must keep the original **Host/SNI** (`s3.example.com`) and only change the IP (a hosts
  entry or an endpoint override). If it connects to `https://<server-ip>:2083` with SNI = the server
  IP, the target's certificate/CDN rejects it;
* putting `<server-ip>:2083` into a proxy client config (v2rayNG, Hiddify, ...) will never work -
  that needs a panel inbound, not a forward;
* verify from outside with
  `curl -k --resolve host:port:<server-ip> https://host:port/` or
  `openssl s_client -connect <server-ip>:<port> -servername host`.

## SNI routing with HAProxy

One public port, several backends, chosen by the TLS `ClientHello` SNI *before* any handshake is
terminated. Useful when one IP must serve several domains, or when you want probes with the wrong
SNI to land on a dead end.

```yaml
# .env
COMPOSE_PROFILES=extra,ha
```

```bash
docker compose up -d
```

Edit `config/haproxy/haproxy.cfg`:

```
acl cdn_ws  req_ssl_sni -i cdn.example.com
use_backend panel_tls if cdn_ws

backend panel_tls
    server edge 172.28.60.1:8443 check
```

`172.28.60.1` is the docker bridge gateway, i.e. the host - correct because the panel runs in host
networking. Use `172.28.60.12` for sing-box (bridge network).

Health checks (`check inter 3s fall 3 rise 2`) take a dead backend out of rotation without a
restart.

## Hiding the IP

DNAT and SNI tricks do not hide anything if DNS still points at you. The only reliable chain is:

1. `A` record -> CDN, proxied.
2. `scripts/cdn-firewall.sh` so the origin answers only CDN ranges.
3. No unproxied subdomain, no mail record, no historical DNS leak (check `crt.sh` and SecurityTrails
   for the domain).
4. REALITY on 443 is the one port that must answer directly - it answers as a normal website.

## Verifying

```bash
sudo nft list table ip outlander
sudo nft list ruleset | grep -A3 outlander
timeout 3 nc -zuv <server-ip> 25000     # hop range should be filtered/closed from outside
ss -lunp | grep -E '8443|8444'
```
