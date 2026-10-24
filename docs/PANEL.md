# Panel (3x-ui) setup

Open `http://<server-ip>:<PANEL_PORT>` and log in with the credentials printed by
`scripts/install.sh` (also stored in `.env`).

## 1. Recommended inbounds

Create these under **Inbounds → Add Inbound**. The panel runs in host networking, so the port you
type is the real host port.

### VLESS + REALITY (best direct protocol)

| field | value |
|---|---|
| Protocol | `vless` |
| Port | `443` |
| Security | `reality` |
| Dest | `www.microsoft.com:443` (or another stable TLS 1.3 site) |
| SNI | same host as `dest` |
| Flow | `xtls-rprx-vision` |
| uTLS fingerprint | `chrome` |
| Short IDs | panel-generated |

Click the eye icon next to the client and copy the `vless://` link. REALITY looks exactly like a
normal visit to `dest`: no certificate of yours is ever sent.

### VLESS + WebSocket (behind a CDN)

| field | value |
|---|---|
| Protocol | `vless` |
| Port | `8080` |
| Security | `none` |
| Transport | `ws`, path `/ol`, host = your CDN domain |

The CDN terminates TLS. Do not add a TLS inbound on this port.

### VLESS + WS + TLS (CDN in Full mode)

| field | value |
|---|---|
| Port | `8443` |
| Security | `tls`, certificate from the panel's **Certificates** page |
| Transport | `ws`, path `/ol` |

### Trojan, Shadowsocks, VMess

- Trojan on `2087` with a certificate (the same one as above).
- Shadowsocks-2022 on `8388` (`2022-blake3-aes-128-gcm`, panel-generated key) - cheapest CPU.
- VMess+WS on `8080` with a different path if you still need old clients.

### Hysteria2 / TUIC

These are not Xray protocols and are not created in the panel. They live in
`config/sing-box/config.json`, driven by `.env`:

```bash
sudo bash scripts/cert.sh hy2.example.com
# .env -> HY2_ENABLED=1, HY2_SNI=hy2.example.com
python3 -m outlander render && docker compose --profile extra up -d
```

Client link:

```
hysteria2://<HY2_PASSWORD>@<ip>:8443?sni=hy2.example.com&obfs=salamander&obfs-password=<HY2_OBFS_PASSWORD>&mport=20000-40000
```

`salamander` obfs makes the QUIC handshake look like random UDP. `mport` requires the nftables hop
rules (`sudo bash scripts/forward-apply.sh`).

## 2. Clients and subscriptions

- Each user gets its own client entry; use **Traffic limit**, **Expiry** and **IP limit**.
- The panel's own subscription links work unchanged; they point at the same inbounds.
- Keep one inbound per protocol/carrier family so a blocked carrier only costs one inbound.

## 3. Routing through the gateway

`make panel` writes two things into the panel's Xray template:

```json
{"tag": "ol-gateway", "protocol": "socks", "settings": {"servers": [{"address": "127.0.0.1", "port": 10808}]}}
```

and a final catch-all rule `{"type": "field", "network": "tcp,udp", "outboundTag": "ol-gateway"}`.

To verify, open **Settings → Xray Configs** and look for the `ol-gateway` outbound and the rule. If
a panel upgrade regenerates the template, run `make panel` again.

If you want panel-managed split routing (e.g. domestic sites direct from the panel instead of the
gateway), add rules **above** the catch-all: rules are evaluated top-down.

## 4. Hardening

- Change `PANEL_PORT` to something high and random; set a **web base path** in panel settings.
- Enable **two-factor auth** for the panel account.
- Panel's fail2ban is on (`XUI_ENABLE_FAIL2BAN=true`).
- Do not expose the panel on a domain that resolves to your origin IP if the panel IP is supposed to
  stay hidden - reach it over your tunnel or a private network instead.
