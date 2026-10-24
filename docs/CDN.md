# CDN fronting

The point: clients talk to an edge IP that belongs to a large CDN. Your origin IP never appears in
DNS, and blocking the edge means blocking thousands of unrelated sites.

## 1. Provider setup

Works with any WebSocket-capable CDN. The three that are actually usable from Iran:

| provider | TLS to origin | port to origin | notes |
|---|---|---|---|
| Cloudflare | Flexible / Full | 8080 / 8443 | WebSocket on by default; good anycast, gets blocked by SNI sometimes |
| CloudyFa | plain HTTP | 80 / 8080 | the layout the example link uses (`:80`, `host=link.xxx`) |
| ArvanCloud | plain / Full | 8080 / 8443 | domestic anycast, less blocked, Iranian IPs |

Steps:

1. Add the domain, then an `A` record `cdn.example.com -> <origin ip>` with the proxy toggle **on**.
2. TLS mode: `Flexible`/`Off` if you terminate at the CDN and send plain WS to `:8080`;
   `Full (strict)` if you send TLS to `:8443` (needs `scripts/cert.sh` for that domain).
3. Keep WebSockets enabled. Disable caching for the WS path.
4. Do **not** create a second, unproxied record for the same hostname. That leaks the origin.

## 2. Origin parameters

For a VLESS+WS inbound in the panel (port 8080, path `/ol`):

```
vless://<uuid>@cdn.example.com:443
  ?encryption=none&security=tls&type=ws
  &host=cdn.example.com&path=%2Fol&sni=cdn.example.com
  &fp=chrome&alpn=http%2F1.1
#CDN
```

- `host` = the CDN hostname (this is what the CDN routes on).
- `path` = the inbound's WS path, url-encoded (`/ol` -> `%2Fol`).
- Client connects to port 443 at the edge; the CDN speaks to origin `:8080`.

If the provider gives you a non-standard edge port, put it in the client link - the origin port
never changes.

## 3. Lock the origin

```bash
sudo bash scripts/cdn-firewall.sh
sudo ADMIN_IPS='203.0.113.7,198.51.100.0/24' bash scripts/cdn-firewall.sh
```

This creates an nftables table that drops traffic to `80/8080/8443` unless the source is a
Cloudflare range (or an allowed admin range). For CloudyFa or ArvanCloud, either extend the script's
`CF4` source or add their published ranges via `ADMIN_IPS`.

Verify from a random host:

```bash
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://<origin-ip>:8080/     # should time out
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' https://cdn.example.com/     # should answer
```

The origin still answers on `443` for REALITY - that is intended: REALITY must be reachable
directly, and it looks like a normal website.

## 4. Multiple upstreams

One origin can host many CDN outbounds. Each upstream link gets its own channel:

```bash
python3 -m outlander add '<vless://...cloudyfa...>'  --tag cf-cdn1 --group cdn
python3 -m outlander add '<vless://...arvan...>'     --tag ar-cdn1 --group cdn
python3 -m outlander add '<vless://...cloudflare...>' --tag cl-cdn1 --group cdn
python3 -m outlander render && docker compose restart gateway
```

The gateway probes all three every minute and sends traffic through the fastest one, falling back
automatically. `python3 -m outlander list` shows what is loaded.

## 5. When the CDN host cannot be resolved

Some ISPs poison the CDN hostname. Pin it:

```bash
python3 -m outlander add '<link>' --tag cf-cdn1 --resolve 104.16.x.x
```

The render adds a `dns.hosts` entry so the gateway never asks the system resolver for that name.

## 6. Troubleshooting

| symptom | check |
|---|---|
| CDN 502/521 | origin port open? panel inbound path matches the link path? |
| CDN 403 | host header mismatch, or the CDN requires TLS to origin |
| connects but no traffic | WS path wrong, or inbound has no matching client UUID |
| works, then dies after minutes | CDN idle timeout - enable WS keepalive / `tcpNoDelay`, or reduce client mux |
| origin IP known to the censor | a DNS history record, or an unproxied subdomain |
