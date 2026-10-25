"""Xray dokodemo-door tunnel forwards.

Rules whose effective mode is ``dokodemo`` are served by a small host-network
Xray instance (``ol-tunnel``): one ``dokodemo-door`` inbound per rule, all of
them dialled through the gateway SOCKS port so the traffic exits over the CDN
chain instead of from the server directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import forward

TUNNEL_CONFIG = Path("config/xray/tunnel.json")


def dokodemo_inbounds(data: dict) -> list[dict]:
    inbounds: list[dict] = []
    for raw in data.get("rules", []):
        rule = forward.normalise_rule(raw)
        if forward.effective_mode(rule) != "dokodemo":
            continue
        host, _, port = rule["target"].partition(":")
        if not host or not port.isdigit():
            continue
        network = "tcp,udp" if rule["proto"] == "both" else rule["proto"]
        inbounds.append(
            {
                "tag": f"fwd-{rule['listen']}",
                "protocol": "dokodemo-door",
                "listen": "0.0.0.0",
                "port": int(rule["listen"]),
                "settings": {
                    "address": host,
                    "port": int(port),
                    "network": network,
                    "followRedirect": False,
                    "timeout": 0,
                },
                "streamSettings": {"network": "tcp", "security": "none"},
                "sniffing": {"enabled": False, "destOverride": [], "metadataOnly": False},
            }
        )
    return inbounds


def config(data: dict, env: dict | None = None) -> dict:
    env = env or {}
    socks_port = int(env.get("GATEWAY_SOCKS_PORT", 10808))
    return {
        "log": {"loglevel": "warning"},
        "inbounds": dokodemo_inbounds(data),
        "outbounds": [
            {
                "tag": "gw",
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": socks_port}]},
            }
        ],
    }


def render(root: Path, env: dict | None = None) -> Path:
    data = forward.load(root)
    path = root / TUNNEL_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config(data, env), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
