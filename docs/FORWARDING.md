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
python3 -m outlander forward render
sudo bash scripts/forward-apply.sh
python3 -m outlander forward list
```

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
