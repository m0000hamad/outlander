"""Build Xray-core outbound objects from parsed share links."""

from __future__ import annotations

from .uri import Link, LinkError, truthy


def _stream(link: Link) -> dict:
    p = link.params
    network = link.network
    security = link.security
    host = link.host
    path = link.path
    stream: dict = {"network": network, "security": security}

    if security == "tls":
        tls: dict = {"serverName": link.sni}
        if p.get("alpn"):
            tls["alpn"] = [item for item in p["alpn"].split(",") if item]
        if p.get("fp"):
            tls["fingerprint"] = p["fp"]
        if truthy(p.get("allowinsecure") or p.get("insecure") or "0"):
            tls["allowInsecure"] = True
        stream["tlsSettings"] = tls
    elif security == "reality":
        reality = {
            "serverName": link.sni,
            "fingerprint": p.get("fp") or "chrome",
            "publicKey": p.get("pbk") or "",
            "shortId": p.get("sid") or "",
            "spiderX": p.get("spx") or "/",
        }
        stream["realitySettings"] = {key: value for key, value in reality.items() if value}

    if network == "ws":
        ws: dict = {"path": path}
        if host:
            ws["headers"] = {"Host": host}
        if p.get("ed") and p["ed"].isdigit():
            ws["maxEarlyData"] = int(p["ed"])
            ws["earlyDataHeaderName"] = p.get("eh") or "Sec-WebSocket-Protocol"
        stream["wsSettings"] = ws
    elif network == "grpc":
        grpc = {"serviceName": p.get("servicename") or path.strip("/")}
        if p.get("mode") == "multi":
            grpc["multiMode"] = True
        stream["grpcSettings"] = grpc
    elif network == "httpupgrade":
        upgrade: dict = {"path": path}
        if host:
            upgrade["host"] = host
        stream["httpupgradeSettings"] = upgrade
    elif network in {"xhttp", "splithttp"}:
        xhttp: dict = {"path": path}
        if host:
            xhttp["host"] = host
        if p.get("mode"):
            xhttp["mode"] = p["mode"]
        stream["xhttpSettings"] = xhttp
    elif network == "http":
        stream["httpSettings"] = {"host": [host] if host else [], "path": path}
    elif network == "kcp":
        kcp: dict = {}
        if p.get("seed"):
            kcp["seed"] = p["seed"]
        header_type = p.get("headertype") or "none"
        kcp["header"] = {"type": header_type}
        stream["kcpSettings"] = kcp
    elif network == "quic":
        stream["quicSettings"] = {
            "security": p.get("quicsecurity") or "none",
            "key": p.get("key") or "",
            "header": {"type": p.get("headertype") or "none"},
        }
    elif network == "tcp" and (p.get("headertype") or "none") == "http":
        stream["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {"path": [path], "headers": {"Host": [host] if host else []}},
            }
        }

    stream["sockopt"] = {"tcpNoDelay": True, "domainStrategy": "UseIP"}
    return stream


def xray_outbound(link: Link, tag: str, via: str | None = None) -> dict:
    """Create an Xray outbound for a link that Xray can speak natively."""
    address, port = link.address, link.port

    if link.scheme == "vless":
        user = {"id": link.uuid, "encryption": link.params.get("encryption") or "none"}
        if link.flow:
            user["flow"] = link.flow
        outbound = {
            "tag": tag,
            "protocol": "vless",
            "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
        }
    elif link.scheme == "vmess":
        user = {
            "id": link.uuid,
            "alterId": int(link.params.get("aid") or 0),
            "security": link.params.get("scy") or "auto",
        }
        outbound = {
            "tag": tag,
            "protocol": "vmess",
            "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
        }
    elif link.scheme == "trojan":
        server: dict = {"address": address, "port": port, "password": link.password}
        if link.flow:
            server["flow"] = link.flow
        outbound = {"tag": tag, "protocol": "trojan", "settings": {"servers": [server]}}
    elif link.scheme == "ss":
        outbound = {
            "tag": tag,
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": address,
                        "port": port,
                        "method": link.method,
                        "password": link.password,
                        "uot": True,
                    }
                ]
            },
        }
    elif link.scheme in {"socks", "http"}:
        server = {"address": address, "port": port}
        if link.uuid or link.password:
            server["users"] = [{"user": link.uuid, "pass": link.password}]
        outbound = {"tag": tag, "protocol": link.scheme, "settings": {"servers": [server]}}
    else:
        raise LinkError(f"{link.scheme}: Xray cannot dial this protocol, use sing-box")

    outbound["streamSettings"] = _stream(link)
    if via:
        outbound["proxySettings"] = {"tag": via, "transportLayer": False}
    return outbound


def direct_outbound(tag: str = "direct") -> dict:
    return {
        "tag": tag,
        "protocol": "freedom",
        "settings": {"domainStrategy": "UseIP"},
        "streamSettings": {"sockopt": {"domainStrategy": "UseIP"}},
    }


def block_outbound(tag: str = "block") -> dict:
    return {"tag": tag, "protocol": "blackhole", "settings": {}}
