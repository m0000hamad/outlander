# Protocols

Everything below is delivered through the same gateway, so one client can mix them freely: the
upstream side (CDN chain) is identical for all of them.

## Comparison

| protocol | transport | TLS | DPI profile | best for |
|---|---|---|---|---|
| VLESS + REALITY | tcp (+vision) | real TLS of a foreign site | indistinguishable from visiting that site | direct-IP clients, the default choice |
| VLESS + WS + TLS | tcp | own cert or CDN | looks like HTTPS to a CDN host | behind a CDN, hides the origin |
| VLESS + WS (plain) | tcp | none | plain HTTP with a Host header | providers that terminate TLS (CloudyFa style) |
| VMess + WS | tcp | own/CDN | older fingerprint, still works | legacy clients only |
| Trojan | tcp | own cert | HTTPS with a password | simple, needs a clean cert |
| Shadowsocks-2022 | tcp+udp | none (AEAD) | random bytes, no handshake to match | low CPU, high throughput, poor against active probes |
| Hysteria2 | QUIC/UDP | own cert + salamander | random UDP, port hopping | lossy mobile links, gaming, high RTT |
| TUIC | QUIC/UDP | own cert | QUIC | alternative when Hysteria2 UDP is throttled |

## Choosing a set

- **Direct connections**: REALITY on 443. It survives active probing because the server performs a
  real handshake with `dest` for anyone without the right keys.
- **CDN**: WS(+TLS) on 8080/8443. Only this family can hide the origin IP.
- **UDP-friendly clients**: Hysteria2 with obfs + port hopping. Keep `mport` and the base port
  published; the nftables hop rule maps the whole range.
- **Volume / cheap CPU**: SS-2022.

Keep at least one REALITY and one CDN inbound active. Networks that throttle UDP break Hysteria2
first; networks that block `microsoft.com` SNI break REALITY.

## Parameter reference (client side)

REALITY:

```
vless://<uuid>@<ip>:443?encryption=none&security=reality&sni=www.microsoft.com
  &fp=chrome&pbk=<public-key>&sid=<short-id>&type=tcp&flow=xtls-rprx-vision#R
```

WS behind CDN:

```
vless://<uuid>@<cdn-host>:443?encryption=none&security=tls&type=ws
  &host=<cdn-host>&path=%2Fol&sni=<cdn-host>#CDN
```

Hysteria2:

```
hysteria2://<password>@<ip>:8443?sni=hy2.example.com&obfs=salamander
  &obfs-password=<obfs-password>&mport=20000-40000#HY2
```

TUIC:

```
tuic://<uuid>:<password>@<ip>:8444?sni=hy2.example.com&congestion_control=bbr&alpn=h3#TUIC
```

## What the gateway does to an upstream link

`outlander add` parses the link and produces a full Xray/sing-box outbound:

| link part | rendered as |
|---|---|
| uuid / password / method | `vnext.users` / `servers.password` / `method` |
| `type=ws` | `wsSettings.path` + `headers.Host` |
| `security=tls` | `tlsSettings` with `serverName`, `alpn`, `fingerprint`, `allowInsecure` |
| `security=reality` | `realitySettings` with `sni`, `pbk`, `sid`, `spx` |
| `type=grpc` / `httpupgrade` / `xhttp` / `http` | matching `*Settings` block |
| `flow=xtls-rprx-vision` | `users[0].flow` |
| `--via <tag>` | `proxySettings.tag` (dial through another outbound first) |

If a link does not parse, the CLI refuses to save it - `render` never produces a half-valid config.
