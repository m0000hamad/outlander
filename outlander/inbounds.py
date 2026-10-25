"""Build VLESS inbounds (REALITY / WebSocket) and client links for them."""

from __future__ import annotations

from urllib.parse import quote

DEFAULT_DEST = "www.microsoft.com:443"
DEFAULT_WS_PATH = "/ol"


def _client(uuid: str, email: str, flow: str = "") -> dict:
    entry = {
        "id": uuid,
        "email": email,
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": "",
        "subId": "",
        "reset": 0,
    }
    if flow:
        entry["flow"] = flow
    return entry


def reality_inbound(
    port: int,
    remark: str,
    uuid: str,
    private_key: str,
    public_key: str,
    short_id: str,
    dest: str = DEFAULT_DEST,
) -> dict:
    server_name = dest.rsplit(":", 1)[0]
    return {
        "enable": True,
        "remark": remark,
        "listen": "",
        "port": int(port),
        "protocol": "vless",
        "expiryTime": 0,
        "total": 0,
        "settings": {
            "clients": [_client(uuid, remark, "xtls-rprx-vision")],
            "decryption": "none",
            "fallbacks": [],
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "externalProxy": [],
            "realitySettings": {
                "show": False,
                "xver": 0,
                "dest": dest,
                "serverNames": [server_name],
                "privateKey": private_key,
                "shortIds": [short_id],
                "settings": {
                    "publicKey": public_key,
                    "fingerprint": "chrome",
                    "serverName": "",
                    "spiderX": "/",
                },
            },
            "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "metadataOnly": False,
            "routeOnly": False,
        },
    }


def ws_inbound(port: int, remark: str, uuid: str, path: str = DEFAULT_WS_PATH) -> dict:
    return {
        "enable": True,
        "remark": remark,
        "listen": "",
        "port": int(port),
        "protocol": "vless",
        "expiryTime": 0,
        "total": 0,
        "settings": {
            "clients": [_client(uuid, remark)],
            "decryption": "none",
            "fallbacks": [],
        },
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "externalProxy": [],
            "wsSettings": {"path": path, "host": "", "headers": {}},
            "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls"],
            "metadataOnly": False,
            "routeOnly": False,
        },
    }


def reality_link(host: str, port: int, uuid: str, public_key: str, short_id: str,
                 dest: str = DEFAULT_DEST, remark: str = "") -> str:
    server_name = dest.rsplit(":", 1)[0]
    fragment = quote(remark) if remark else ""
    return (
        f"vless://{uuid}@{host}:{port}?type=tcp&security=reality&encryption=none"
        f"&pbk={public_key}&fp=chrome&sni={server_name}&sid={short_id}"
        f"&flow=xtls-rprx-vision#{fragment}"
    )


def ws_tls_inbound(
    port: int,
    remark: str,
    uuid: str,
    domain: str,
    certificate_file: str,
    key_file: str,
    path: str = DEFAULT_WS_PATH,
) -> dict:
    """VLESS + WS + TLS, with the certificate read from the panel's mount."""
    return {
        "enable": True,
        "remark": remark,
        "listen": "",
        "port": int(port),
        "protocol": "vless",
        "expiryTime": 0,
        "total": 0,
        "settings": {
            "clients": [_client(uuid, remark)],
            "decryption": "none",
            "fallbacks": [],
        },
        "streamSettings": {
            "network": "ws",
            "security": "tls",
            "externalProxy": [],
            "tlsSettings": {
                "serverName": domain,
                "alpn": ["http/1.1"],
                "certificates": [
                    {"certificateFile": certificate_file, "keyFile": key_file}
                ],
            },
            "wsSettings": {"path": path, "host": domain, "headers": {}},
            "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls"],
            "metadataOnly": False,
            "routeOnly": False,
        },
    }


def ws_link(host: str, port: int, uuid: str, path: str = DEFAULT_WS_PATH, remark: str = "") -> str:
    fragment = quote(remark) if remark else ""
    return (
        f"vless://{uuid}@{host}:{port}?type=ws&security=none&encryption=none"
        f"&path={quote(path, safe='')}&host={host}#{fragment}"
    )


def ws_tls_link(host: str, port: int, uuid: str, sni: str, path: str = DEFAULT_WS_PATH, remark: str = "") -> str:
    fragment = quote(remark) if remark else ""
    return (
        f"vless://{uuid}@{host}:{port}?type=ws&security=tls&encryption=none"
        f"&sni={sni}&host={sni}&path={quote(path, safe='')}&fp=chrome#{fragment}"
    )


def link_for(inbound: dict, host: str) -> str:
    """Client link for an inbound stored by the panel (empty when unsupported)."""
    if inbound.get("protocol") != "vless":
        return ""
    stream = inbound.get("streamSettings") or {}
    try:
        uuid = inbound["settings"]["clients"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return ""
    port = inbound.get("port")
    remark = inbound.get("remark") or ""
    security = (stream.get("security") or "none").lower()

    if security == "reality":
        reality = stream.get("realitySettings") or {}
        settings = reality.get("settings") or {}
        public_key = settings.get("publicKey") or ""
        names = reality.get("serverNames") or []
        dest = reality.get("dest") or (names[0] + ":443" if names else DEFAULT_DEST)
        short_ids = reality.get("shortIds") or [""]
        if not public_key:
            return ""
        return reality_link(host, port, uuid, public_key, short_ids[0], dest, remark)

    if (stream.get("network") or "").lower() == "ws" and security in {"none", ""}:
        path = (stream.get("wsSettings") or {}).get("path") or DEFAULT_WS_PATH
        return ws_link(host, port, uuid, path, remark)

    if (stream.get("network") or "").lower() == "ws" and security == "tls":
        ws = stream.get("wsSettings") or {}
        path = ws.get("path") or DEFAULT_WS_PATH
        sni = (stream.get("tlsSettings") or {}).get("serverName") or ws.get("host") or host
        return ws_tls_link(sni, port, uuid, sni, path, remark)
    return ""
