"""sing-box pieces: Hysteria2/TUIC outbounds plus the matching local SOCKS inbounds.

Xray cannot speak Hysteria2 or TUIC, so those channels live in sing-box and are
handed to the gateway through a loopback SOCKS5 inbound with auth.
"""

from __future__ import annotations

from .uri import Link, LinkError, truthy


def _tls(link: Link, alpn: str = "h3") -> dict:
    tls: dict = {"enabled": True, "server_name": link.sni}
    if link.params.get("alpn"):
        tls["alpn"] = [item for item in link.params["alpn"].split(",") if item]
    else:
        tls["alpn"] = [alpn]
    if truthy(link.params.get("insecure") or link.params.get("allowinsecure") or "0"):
        tls["insecure"] = True
    return tls


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
