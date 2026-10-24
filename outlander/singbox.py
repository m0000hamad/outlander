"""sing-box pieces.

Two jobs:

* speak the protocols Xray cannot (Hysteria2/TUIC)
* speak the protocols new Xray versions refuse without TLS (plain VLESS/Trojan,
  which is exactly what a CDN-fronted ``ws`` link looks like)

Either way the outbound is exposed to the gateway through a loopback SOCKS5
inbound with a password derived from the inventory secret.
"""

from __future__ import annotations

from .uri import Link, LinkError, truthy

BRIDGE_SCHEMES = {"hysteria2", "tuic"}


def needs_bridge(link: Link) -> bool:
    """True when the outbound must live in sing-box instead of Xray."""
    if link.scheme in BRIDGE_SCHEMES:
        return True
    if link.scheme in {"vless", "trojan"} and link.security not in {"tls", "reality"}:
        # Xray >= 26.10 refuses "vless/trojan without TLS" towards public addresses.
        return True
    return False


def _tls(link: Link, alpn: str = "h3") -> dict:
    tls: dict = {"enabled": True, "server_name": link.sni}
    if link.params.get("alpn"):
        tls["alpn"] = [item for item in link.params["alpn"].split(",") if item]
    else:
        tls["alpn"] = [alpn]
    if truthy(link.params.get("insecure") or link.params.get("allowinsecure") or "0"):
        tls["insecure"] = True
    return tls


def _transport(link: Link) -> dict | None:
    network = link.network
    if network == "ws":
        transport: dict = {"type": "ws", "path": link.path}
        if link.host:
            transport["headers"] = {"Host": link.host}
        return transport
    if network == "grpc":
        return {"type": "grpc", "service_name": link.params.get("servicename") or link.path.strip("/")}
    if network == "httpupgrade":
        upgrade: dict = {"type": "httpupgrade", "path": link.path}
        if link.host:
            upgrade["host"] = link.host
        return upgrade
    if network == "http":
        plain: dict = {"type": "http", "path": link.path}
        if link.host:
            plain["host"] = [link.host]
        return plain
    if network == "tcp":
        if (link.params.get("headertype") or "none") == "http":
            raise LinkError(
                "tcp+http-header without TLS cannot be bridged; use a ws link or enable TLS"
            )
        return None
    raise LinkError(
        f"'{network}' without TLS cannot be bridged through sing-box; use a ws link or enable TLS"
    )


def singbox_outbound(link: Link, tag: str) -> dict:
    if link.scheme == "hysteria2":
        outbound: dict = {
            "type": "hysteria2",
            "tag": tag,
            "server": link.address,
            "server_port": link.port,
            "password": link.password,
            "tls": _tls(link),
        }
        up = link.params.get("up") or link.params.get("upmbps") or link.params.get("up_mbps")
        down = link.params.get("down") or link.params.get("downmbps") or link.params.get("down_mbps")
        if up and up.isdigit():
            outbound["up_mbps"] = int(up)
        if down and down.isdigit():
            outbound["down_mbps"] = int(down)
        if link.params.get("obfs"):
            outbound["obfs"] = {
                "type": link.params["obfs"],
                "password": link.params.get("obfs-password") or link.params.get("obfspassword") or "",
            }
        return outbound

    if link.scheme == "tuic":
        return {
            "type": "tuic",
            "tag": tag,
            "server": link.address,
            "server_port": link.port,
            "uuid": link.uuid,
            "password": link.password,
            "congestion_control": link.params.get("congestion_control") or "bbr",
            "udp_relay_mode": link.params.get("udp_relay_mode") or "native",
            "tls": _tls(link),
        }

    if link.scheme == "vless":
        outbound = {
            "type": "vless",
            "tag": tag,
            "server": link.address,
            "server_port": link.port,
            "uuid": link.uuid,
            "packet_encoding": "xudp",
        }
        transport = _transport(link)
        if transport:
            outbound["transport"] = transport
        return outbound

    if link.scheme == "trojan":
        outbound = {
            "type": "trojan",
            "tag": tag,
            "server": link.address,
            "server_port": link.port,
            "password": link.password,
        }
        transport = _transport(link)
        if transport:
            outbound["transport"] = transport
        return outbound

    if link.scheme == "vmess":
        outbound = {
            "type": "vmess",
            "tag": tag,
            "server": link.address,
            "server_port": link.port,
            "uuid": link.uuid,
            "alter_id": int(link.params.get("aid") or 0),
            "security": link.params.get("scy") or "auto",
        }
        transport = _transport(link)
        if transport:
            outbound["transport"] = transport
        return outbound

    raise LinkError(f"{link.scheme}: sing-box cannot dial this protocol")


def socks_inbound(tag: str, port: int, username: str, password: str) -> dict:
    return {
        "type": "socks",
        "tag": tag,
        "listen": "0.0.0.0",
        "listen_port": port,
        "users": [{"username": username, "password": password}],
    }


def hysteria2_inbound(env: dict) -> dict:
    return {
        "type": "hysteria2",
        "tag": "hy2-in",
        "listen": "::",
        "listen_port": int(env.get("HY2_PORT", 8443)),
        "users": [{"password": env.get("HY2_PASSWORD", "change-me")}],
        "obfs": {
            "type": "salamander",
            "password": env.get("HY2_OBFS_PASSWORD", "change-me"),
        },
        "tls": {
            "enabled": True,
            "server_name": env.get("HY2_SNI", "example.com"),
            "alpn": ["h3"],
            "certificate_path": env.get("HY2_CERT", "/certs/fullchain.pem"),
            "key_path": env.get("HY2_KEY", "/certs/privkey.pem"),
        },
        "masquerade": f"https://{env.get('HY2_SNI', 'example.com')}/",
    }


def tuic_inbound(env: dict) -> dict:
    return {
        "type": "tuic",
        "tag": "tuic-in",
        "listen": "::",
        "listen_port": int(env.get("TUIC_PORT", 8444)),
        "users": [{"uuid": env.get("TUIC_UUID", ""), "password": env.get("TUIC_PASSWORD", "")}],
        "congestion_control": "bbr",
        "auth_timeout": "3s",
        "zero_rtt_handshake": False,
        "tls": {
            "enabled": True,
            "server_name": env.get("TUIC_SNI") or env.get("HY2_SNI", "example.com"),
            "alpn": ["h3"],
            "certificate_path": env.get("HY2_CERT", "/certs/fullchain.pem"),
            "key_path": env.get("HY2_KEY", "/certs/privkey.pem"),
        },
    }
