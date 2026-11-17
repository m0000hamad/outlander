"""Owned Linux GRE interfaces for explicitly selected forwarding rules."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import subprocess
from pathlib import Path

FIELDS = ("gre_local", "gre_remote", "gre_address")
OWNER = "outlander-gre:"


def validate(rule: dict) -> dict:
    result = {}
    for key in FIELDS:
        try:
            address = ipaddress.IPv4Address(str(rule.get(key) or "").strip())
        except ipaddress.AddressValueError as exc:
            raise ValueError(f"{key} must be an IPv4 address") from exc
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved:
            raise ValueError(f"{key} must be a unicast IPv4 address")
        result[key] = str(address)
    try:
        peer = ipaddress.IPv4Address(rule["target"].partition(":")[0])
    except ipaddress.AddressValueError as exc:
        raise ValueError("GRE target must be the remote tunnel IPv4 address, optionally with a port") from exc
    if peer.is_unspecified or peer.is_multicast or peer.is_loopback or peer.is_reserved:
        raise ValueError("GRE target must be a unicast tunnel IPv4 address")
    if len(set(result.values()) | {str(peer)}) != 4:
        raise ValueError("GRE outer endpoints and inner tunnel addresses must be distinct")
    return result


def profiles(data: dict) -> dict[str, dict]:
    # Import here to keep forwarding validation free of import cycles.
    from .forward import normalise_rule
    desired = {}
    for raw in data.get("rules", []):
        if raw.get("mode") != "gre":
            continue
        rule = normalise_rule(raw)
        profile = {key: rule[key] for key in FIELDS}
        profile["peer"] = rule["target"].partition(":")[0]
        endpoints = [profile["gre_local"], profile["gre_remote"]]
        name = "olgre" + hashlib.sha256(json.dumps(endpoints).encode()).hexdigest()[:10]
        if name in desired and desired[name] != profile:
            raise ValueError("GRE rules sharing outer endpoints must use identical inner tunnel addresses")
        if any(p["peer"] == profile["peer"] and p != profile for p in desired.values()):
            raise ValueError("GRE rules for one tunnel target must use identical endpoints and inner address")
        desired[name] = profile
    outer = {p[key] for p in desired.values() for key in ("gre_local", "gre_remote")}
    if any(p["peer"] in outer or p["gre_address"] in outer for p in desired.values()):
        raise ValueError("GRE inner addresses must not overlap any outer endpoint")
    return desired


def _ip(*args: str) -> str:
    result = subprocess.run(["ip", *args], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "ip command failed").strip())
    return result.stdout


def apply(root: Path, data: dict, *, cleanup: bool = False) -> None:
    desired = profiles(data)
    marker = root / "data/gre-active"
    if not desired and not marker.exists():
        return
    links = {link["ifname"]: link for link in json.loads(_ip("-j", "link", "show"))}
    # Refuse collisions before modifying any interface. Never claim other links.
    for name in desired:
        if name in links and links[name].get("ifalias") != OWNER + name:
            raise RuntimeError(f"GRE interface name is already in use: {name}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    for name, profile in desired.items():
        if name not in links:
            _ip("tunnel", "add", name, "mode", "gre", "local", profile["gre_local"],
                "remote", profile["gre_remote"], "ttl", "64")
            try:
                _ip("link", "set", "dev", name, "alias", OWNER + name)
            except Exception:
                _ip("link", "delete", "dev", name)
                raise
        addresses = json.loads(_ip("-j", "address", "show", "dev", name))
        current = [a for link in addresses for a in link.get("addr_info", []) if a.get("family") == "inet"]
        if current and any(a.get("local") != profile["gre_address"] or a.get("address") != profile["peer"]
                           or a.get("prefixlen") != 32 for a in current):
            _ip("-4", "address", "flush", "dev", name)
        _ip("address", "replace", profile["gre_address"] + "/32", "peer", profile["peer"] + "/32", "dev", name)
        _ip("link", "set", "dev", name, "mtu", "1400", "up")
        # Move the peer route before removing an old interface after endpoint edits.
        _ip("route", "replace", profile["peer"] + "/32", "dev", name, "src", profile["gre_address"])
    if cleanup:
        for name, link in links.items():
            if link.get("ifalias") == OWNER + name and name not in desired:
                _ip("link", "delete", "dev", name)
        if not desired:
            marker.unlink(missing_ok=True)


if __name__ == "__main__":
    import sys
    from .forward import load
    root = Path.cwd()
    apply(root, {"rules": []} if "--clean" in sys.argv else load(root), cleanup=True)
