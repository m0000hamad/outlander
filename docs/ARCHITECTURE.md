# Architecture

## Traffic path

```
                    ┌──────────────────────────── ol-panel (host net) ───────────────────────────┐
 client ──tls──►    │ 3x-ui Xray inbounds: VLESS+REALITY 443, WS 8080, WS+TLS 8443,             │
                    │ Trojan 2087, SS-2022 8388                                                  │
                    └───────────────────────────────┬────────────────────────────────────────────┘
                                                    │ outbound "ol-gateway" (socks5, routing catch-all)
                                                    ▼
                    ┌──────────────────────────── ol-gateway (bridge, 172.28.60.10) ─────────────┐
                    │ inbounds: socks 10808, http 10809 (127.0.0.1 published only)                │
                    │ routing:  private/IR -> direct, ads -> block, everything -> balancer        │
                    │ balancer: leastPing over the burstObservatory across all ol-* outbounds     │
                    │ outbounds: CDN chains (vless/vmess/trojan/ss/...) + socks bridges to sing-box│
                    └───────────┬──────────────────────────────┬─────────────────────────────────┘
                                │                              │
                    ┌───────────▼───────────┐      ┌───────────▼─────────────┐
                    │ CDN edge (provider)   │      │ ol-singbox (172.28.60.12)│
                    │ host: cdn.example.ir  │      │ hy2/tuic outbound +      │
                    │ path: /link/<token>   │      │ authenticated socks in    │
                    └───────────┬───────────┘      └──────────────────────────┘
                                ▼
                        upstream server (abr02...)  ──► international internet
```

`ol-console` is the control plane: a tiny web UI with the docker socket mounted, host networking and `NET_ADMIN`. It edits `config/inventory.json` / `config/forwarding.json`, re-renders, restarts the gateway and reloads nftables. It sits outside the data path, so a console restart never drops traffic.

`ol-singbox` also serves *inbound* Hysteria2/TUIC for clients and sends their egress back to
`ol-gateway` (`route.final = gw`). One routing plane, two protocol families.

## Why a separate gateway instead of panel outbounds directly

| concern | gateway | panel |
|---|---|---|
| hot-swapping upstreams | `outlander add` + `render` + restart | template patch, panel restart |
| failover | `leastPing` balancer + observatory | not supported |
| credential isolation | `inventory.json` only | Xray template inside the panel DB |
| blast radius | one small container | panels regenerate the template on upgrades |

The panel only needs a single stable outbound (`127.0.0.1:10808`), so a panel upgrade cannot
break the upstream chain. Re-run `make panel` after any panel-side Xray edit.

## Why some channels live in sing-box

Two cases:

1. **Hysteria2 / TUIC** - Xray has no QUIC client for either, sing-box implements both.
2. **VLESS or Trojan without TLS** - Xray `>= 26.10` refuses those towards any public address
   (`vless without TLS or other encryption is prohibited unless the server address is a private IP
   or domain`). A CDN-fronted `ws://host:80` link is exactly that shape, so the renderer puts those
   outbounds in sing-box instead of Xray. Nothing changes in the inventory: `needs_bridge()` decides
   and the gateway sees one more `socks` outbound.

The gateway reaches each sing-box outbound through a loopback SOCKS5 inbound that requires a
password derived from the inventory secret, so nothing is exposed unauthenticated. sing-box routes
each bridge inbound to its matching outbound (`route.rules[inbound -> outbound]`) and sends
Hysteria2/TUIC client traffic back to `gw`, the gateway.

## Config generation

```
config/inventory.json ──┐
                        ├─► python3 -m outlander render ─► config/xray/config.json
.env ───────────────────┘                                └► config/sing-box/config.json
config/forwarding.json ───► python3 -m outlander forward render ─► config/nftables/outlander.nft
```

`inventory.json` stores the **raw share links**; every render re-parses them wholesale. There is
one source of truth per object and no partially-migrated configs.

Tags are deterministic: `ol-<group>-<tag>`. The global balancer matches the `ol-` prefix, so
adding a channel is enough - no routing edits.

## Failure modes

| symptom | cause | action |
|---|---|---|
| everything direct, CDN ignored | no enabled channels / render not applied | `outlander list`, `render`, restart gateway |
| one channel flapping | upstream dead | balancer skips it; remove or change `--tag` |
| DNS for the CDN host fails | ISP poisoning | `--resolve <ip>` |
| panel traffic bypasses chain | template reverted by an upgrade | `make panel` or the console button |
| hy2 handshake fails | cert/SNI/obfs mismatch | `make cert`, compare `.env` with the client |
