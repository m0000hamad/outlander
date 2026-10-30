"""Inventory + config rendering for the outlander gateway and sing-box edge."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from .singbox import (
    hysteria2_inbound,
    needs_bridge,
    singbox_outbound,
    socks_inbound,
    tuic_inbound,
)
from .uri import Link, LinkError, parse, truthy
from .xray import block_outbound, direct_outbound, xray_outbound

INVENTORY = Path("config/inventory.json")
GATEWAY_CONFIG = Path("config/xray/config.json")
SINGBOX_CONFIG = Path("config/sing-box/config.json")
GATEWAY_TAG = "ol-gateway"
SINGBOX_SOCKS_BASE = 11000
SOCKS_USER = "ol"

TAG_RE = re.compile(r"[^a-z0-9-]+")


def slug(value: str) -> str:
    cleaned = TAG_RE.sub("-", value.strip().lower()).strip("-")
    return cleaned or "ch"


def default_inventory() -> dict:
    return {
        "version": 1,
        "secret": secrets.token_urlsafe(24),
        "settings": {
            "direct_domestic": True,
            "block_ads": True,
            "strategy": "leastPing",
            "health_check": "https://www.gstatic.com/generate_204",
        },
        "channels": [],
    }


def load(root: Path) -> dict:
    path = root / INVENTORY
    if not path.exists():
        return default_inventory()
    with path.open("r", encoding="utf-8") as handle:
        inventory = json.load(handle)
    inventory.setdefault("version", 1)
    inventory.setdefault("secret", secrets.token_urlsafe(24))
    inventory.setdefault("settings", default_inventory()["settings"])
    inventory.setdefault("channels", [])
    return inventory


def save(root: Path, inventory: dict) -> None:
    path = root / INVENTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(inventory, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def read_env(root: Path) -> dict:
    import os

    env: dict[str, str] = {}
    path = root / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({key: value for key, value in os.environ.items() if key in env or key.startswith(("HY2_", "TUIC_", "GATEWAY_"))})
    return env


def outbound_tag(channel: dict) -> str:
    return f"ol-{slug(channel.get('group') or 'cdn')}-{slug(channel.get('tag') or 'ch')}"


def find(inventory: dict, tag: str) -> dict | None:
    for channel in inventory["channels"]:
        if channel.get("tag") == tag:
            return channel
    return None


def add_channel(
    inventory: dict,
    link_raw: str,
    tag: str | None = None,
    group: str = "cdn",
    resolve: str = "",
    via: str = "",
) -> dict:
    link = parse(link_raw)
    if needs_bridge(link):
        singbox_outbound(link, "validate", resolve)
    name = tag or (link.name or link.address)
    if find(inventory, name):
        raise LinkError(f"channel '{name}' already exists")
    if via and not find(inventory, via):
        raise LinkError(f"--via target '{via}' does not exist")
    channel = {
        "tag": name,
        "group": group,
        "enabled": True,
        "resolve": resolve,
        "via": via,
        "link": link_raw.strip(),
    }
    inventory["channels"].append(channel)
    return channel


def gateway_config(inventory: dict, env: dict | None = None) -> dict:
    env = env or {}
    settings = inventory.get("settings", {})
    secret = inventory.get("secret", "")
    socks_port = int(env.get("GATEWAY_SOCKS_PORT", 10808))
    http_port = int(env.get("GATEWAY_HTTP_PORT", 10809))

    channels = [channel for channel in inventory["channels"] if channel.get("enabled", True)]
    tags = {channel["tag"]: outbound_tag(channel) for channel in channels}

    outbounds: list[dict] = []
    dns_hosts: dict[str, str] = {}
    socks_index = 0

    for channel in channels:
        link = parse(channel["link"])
        tag = outbound_tag(channel)
        via = tags.get(channel.get("via") or "")
        if needs_bridge(link):
            port = SINGBOX_SOCKS_BASE + socks_index
            socks_index += 1
            outbounds.append(
                {
                    "tag": tag,
                    "protocol": "socks",
                    "settings": {
                        "servers": [
                            {
                                "address": "ol-singbox",
                                "port": port,
                                "users": [{"user": SOCKS_USER, "pass": secret[:24]}],
                            }
                        ]
                    },
                }
            )
        else:
            outbounds.append(xray_outbound(link, tag, via=via))
        if channel.get("resolve"):
            dns_hosts[f"domain:{link.address}"] = channel["resolve"]

    outbounds.append(direct_outbound())
    outbounds.append(block_outbound())

    groups = sorted({slug(channel.get("group") or "cdn") for channel in channels})
    balancers: list[dict] = []
    for group in groups:
        balancers.append(
            {
                "tag": f"ol-bal-{group}",
                "selector": [f"ol-{group}-"],
                "strategy": {"type": settings.get("strategy", "leastPing")},
                "fallbackTag": "direct",
            }
        )
    if channels:
        balancers.append(
            {
                "tag": "ol-auto",
                "selector": ["ol-"],
                "strategy": {"type": settings.get("strategy", "leastPing")},
                "fallbackTag": "direct",
            }
        )

    rules: list[dict] = []
    if channels:
        # Domains must reach the balancer before any IP rule: with IPIfNonMatch the
        # local (often poisoned) resolver would otherwise turn a blocked domain into
        # a private IP and match the "direct" rule below. Domestic domains are still
        # kept direct, and the far end resolves the domain itself.
        if settings.get("direct_domestic", True):
            rules.append(
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "domain": ["domain:ir", "geosite:category-ir"],
                }
            )
        if settings.get("block_ads", True):
            rules.append({"type": "field", "outboundTag": "block", "domain": ["geosite:category-ads-all"]})
        rules.append({"type": "field", "domain": ["regexp:.+"], "balancerTag": "ol-auto"})

    rules.append(
        {
            "type": "field",
            "outboundTag": "direct",
            "ip": [
                "127.0.0.0/8",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "169.254.0.0/16",
                "100.64.0.0/10",
                "::1/128",
                "fc00::/7",
                "fe80::/10",
            ],
        }
    )
    if settings.get("direct_domestic", True):
        rules.append({"type": "field", "outboundTag": "direct", "ip": ["geoip:ir"]})
    if channels:
        rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "ol-auto"})

    config: dict = {
        "log": {"loglevel": "warning", "dnsLog": False},
        "dns": {
            "servers": ["1.1.1.1", "8.8.8.8", "localhost"],
            "queryStrategy": "UseIPv4",
            "disableFallbackIfMatch": False,
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "protocol": "socks",
                "listen": "0.0.0.0",
                "port": socks_port,
                "settings": {"auth": "noauth", "udp": True},
                # Sniffed SNI/Host is routing metadata, never a replacement for
                # the SOCKS destination. A forwarded client's SNI may resolve
                # back to our own listener and otherwise create a connection loop.
                "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
            },
            {
                "tag": "http-in",
                "protocol": "http",
                "listen": "0.0.0.0",
                "port": http_port,
                "settings": {"allowTransparent": False},
            },
        ],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules},
    }
    if balancers:
        config["routing"]["balancers"] = balancers
        config["burstObservatory"] = {
            "subjectSelector": ["ol-"],
            "pingConfig": {
                "destination": settings.get("health_check", "https://www.gstatic.com/generate_204"),
                "interval": "1m",
                "sampling": 3,
                "timeout": "5s",
            },
        }
    if dns_hosts:
        config["dns"]["hosts"] = dns_hosts
    return config


def singbox_config(inventory: dict, env: dict | None = None) -> dict:
    env = env or {}
    secret = inventory.get("secret", "")
    channels = [channel for channel in inventory["channels"] if channel.get("enabled", True)]

    inbounds: list[dict] = []
    outbounds: list[dict] = []
    rules: list[dict] = []
    socks_index = 0
    for channel in channels:
        link = parse(channel["link"])
        if not needs_bridge(link):
            continue
        tag = outbound_tag(channel)
        port = SINGBOX_SOCKS_BASE + socks_index
        socks_index += 1
        outbounds.append(singbox_outbound(link, tag, channel.get("resolve") or ""))
        inbounds.append(socks_inbound(f"{tag}-in", port, SOCKS_USER, secret[:24]))
        rules.append({"inbound": [f"{tag}-in"], "outbound": tag})

    if truthy(env.get("HY2_ENABLED", "0")):
        inbounds.append(hysteria2_inbound(env))
    if truthy(env.get("TUIC_ENABLED", "0")):
        inbounds.append(tuic_inbound(env))

    outbounds.append(
        {
            "type": "socks",
            "tag": "gw",
            "server": "ol-gateway",
            "server_port": int(env.get("GATEWAY_SOCKS_PORT", 10808)),
            "version": "5",
        }
    )
    outbounds.append({"type": "direct", "tag": "direct"})

    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules, "final": "gw"},
    }


def render(root: Path, env: dict | None = None) -> dict[str, Path]:
    env = env if env is not None else read_env(root)
    inventory = load(root)
    save(root, inventory)

    gateway_path = root / GATEWAY_CONFIG
    gateway_path.parent.mkdir(parents=True, exist_ok=True)
    gateway = gateway_config(inventory, env)
    gateway_path.write_text(json.dumps(gateway, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    singbox_path = root / SINGBOX_CONFIG
    singbox_path.parent.mkdir(parents=True, exist_ok=True)
    singbox_payload = singbox_config(inventory, env)
    singbox_path.write_text(json.dumps(singbox_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    from . import tunnel

    tunnel_path = tunnel.render(root, env)

    return {
        "inventory": root / INVENTORY,
        "gateway": gateway_path,
        "singbox": singbox_path,
        "tunnel": tunnel_path,
    }


def panel_fragments(inventory: dict, env: dict | None = None) -> tuple[dict, dict]:
    """Outbound + routing rule that make the 3x-ui panel dial through the gateway."""
    env = env or {}
    outbound = {
        "tag": GATEWAY_TAG,
        "protocol": "socks",
        "settings": {
            "servers": [
                {
                    "address": "127.0.0.1",
                    "port": int(env.get("GATEWAY_SOCKS_PORT", 10808)),
                }
            ]
        },
    }
    rule = {"type": "field", "network": "tcp,udp", "outboundTag": GATEWAY_TAG}
    return outbound, rule
