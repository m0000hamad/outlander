"""Render nftables DNAT rules from config/forwarding.json.

Covers two things the panel cannot do on its own:
  * Hysteria2 port hopping (a UDP range DNAT'd onto the single real listener)
  * plain L4 port/IP forwarding to any internal host or container
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from pathlib import Path

FORWARDING = Path("config/forwarding.json")
NFT_OUTPUT = Path("config/nftables/outlander.nft")

DEFAULT = {
    "hop": {"enabled": True, "proto": "udp", "range": "20000-40000", "target": "172.28.60.12:8443"},
    "rules": [],
}

DOCKER_SUBNET = "172.28.60.0/24"

PORT_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")
IP_RE = re.compile(r"^[0-9a-fA-F:.]{2,45}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}(:\d{1,5})?$")
PROTOS = {"tcp", "udp", "both"}
MODES = {"auto", "nft", "dokodemo"}


def load(root: Path) -> dict:
    path = root / FORWARDING
    if not path.exists():
        return json.loads(json.dumps(DEFAULT))
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("hop", dict(DEFAULT["hop"]))
    data.setdefault("rules", [])
    return data


def save(root: Path, data: dict) -> None:
    path = root / FORWARDING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _target(value: str) -> str:
    """Normalise and validate a forward target (host or host:port)."""
    value = (value or "").strip()
    if not value or not TARGET_RE.match(value):
        raise ValueError(f"invalid target '{value}' (use 10.0.0.5 or 10.0.0.5:443)")
    host, _, port = value.partition(":")
    if not host:
        raise ValueError("forward target must include a host")
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        raise ValueError(f"invalid target port '{port}'")
    return f"{host}:{port}" if port else host


def probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_target(value: str) -> str:
    """Turn a host[:port] target into ip[:port]; nftables cannot resolve names.

    CDN hostnames return several anycast addresses and some of them can be
    unreachable from this host, so the first one that accepts a TCP connection
    is picked. Without a port the first address is used.
    """
    host, _, port = value.partition(":")
    try:
        ipaddress.ip_address(host)
        return value
    except ValueError:
        pass
    try:
        info = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve forward target '{host}': {exc}") from exc
    addresses: list[str] = []
    for entry in info:
        address = entry[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ValueError(f"no IPv4 address for forward target '{host}'")
    chosen = addresses[0]
    if port.isdigit():
        for address in addresses:
            if probe(address, int(port)):
                chosen = address
                break
    return f"{chosen}:{port}" if port else chosen


def normalise_rule(rule: dict) -> dict:
    """Validate and normalise a forwarding rule."""
    proto = (rule.get("proto") or "tcp").strip().lower()
    if proto not in PROTOS:
        raise ValueError("proto must be tcp, udp or both")
    mode = (rule.get("mode") or "nft").strip().lower()
    if mode not in MODES:
        raise ValueError("mode must be auto, nft or dokodemo")
    listen = str(rule.get("listen") if rule.get("listen") is not None else rule.get("listen_port") or "").strip()
    if listen != "*" and not PORT_RE.match(listen):
        raise ValueError("listen must be a port, a range like 50000-50100, or *")
    if "-" in listen:
        low, _, high = listen.partition("-")
        if not 0 < int(low) <= int(high) < 65536:
            raise ValueError("listen range is invalid")
    elif listen != "*" and not 0 < int(listen) < 65536:
        raise ValueError("listen port must be between 1 and 65535")
    listen_ip = str(rule.get("listen_ip") or "").strip()
    if listen_ip and not IP_RE.match(listen_ip):
        raise ValueError("listen_ip must be an IP address")
    if listen == "*" and not listen_ip:
        raise ValueError("forwarding every port needs a listen_ip")
    target = _target(rule.get("target") or "")
    if listen == "*" and ":" in target:
        raise ValueError("forwarding every port cannot change the port; drop the target port")
    if mode == "dokodemo":
        if listen_ip:
            raise ValueError("dokodemo listens on all addresses; drop listen_ip")
        if not listen.isdigit():
            raise ValueError("dokodemo needs a single listen port, not a range")
        if ":" not in target:
            raise ValueError("dokodemo needs a target with a port")
    return {
        "proto": proto,
        "listen": listen,
        "listen_ip": listen_ip,
        "target": target,
        "mode": mode,
        "resolved": str(rule.get("resolved") or ""),
        "inbound_id": rule.get("inbound_id") or None,
        "last_error": str(rule.get("last_error") or ""),
        "checked_at": str(rule.get("checked_at") or ""),
    }


def effective_mode(rule: dict) -> str:
    """Path a rule is actually using right now (empty while auto is undecided)."""
    mode = rule.get("mode") or "nft"
    if mode == "auto":
        return str(rule.get("resolved") or "")
    return mode


def _allow_command(host: str, port: str, proto: str) -> str:
    rule = f"-d {host}"
    if port:
        rule += f" -p {proto} --dport {port}"
    return (
        f"iptables -C DOCKER-USER {rule} -j ACCEPT 2>/dev/null || "
        f"iptables -I DOCKER-USER 1 {rule} -j ACCEPT 2>/dev/null || true"
    )


def listening_ports(proc_net: str = "/proc/net") -> set[int]:
    """Ports with a LISTEN socket in the current network namespace."""
    ports: set[int] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (Path(proc_net) / name).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            try:
                ports.add(int(fields[1].split(":")[1], 16))
            except (IndexError, ValueError):
                continue
    return ports


def socket_inode(port: int, proc_net: str = "/proc/net") -> str:
    """Inode of the LISTEN socket on ``port`` (empty when nothing listens)."""
    for name in ("tcp", "tcp6"):
        try:
            lines = (Path(proc_net) / name).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                if int(fields[1].split(":")[1], 16) == port:
                    return fields[9]
            except (IndexError, ValueError):
                continue
    return ""


def listener_owner(port: int, proc_root: str = "/proc") -> str:
    """Best effort process name holding a listening socket (Linux only)."""
    inode = socket_inode(port, str(Path(proc_root) / "net"))
    if not inode:
        return ""
    target = f"socket:[{inode}]"
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError:
        return ""
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            descriptors = list((entry / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                if os.readlink(descriptor) != target:
                    continue
            except OSError:
                continue
            try:
                name = (entry / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                name = ""
            return f"{name} (pid {entry.name})" if name else f"pid {entry.name}"
    return ""


def conflicting_ports(rule: dict, proc_net: str = "/proc/net") -> list[int]:
    """Ports a DNAT rule would hijack from an already listening service."""
    if rule.get("listen_ip") or rule.get("listen") == "*":
        return []
    listen = str(rule.get("listen") or "")
    busy = listening_ports(proc_net)
    if "-" in listen:
        low, _, high = listen.partition("-")
        if not low.isdigit() or not high.isdigit():
            return []
        return sorted(port for port in busy if int(low) <= port <= int(high))
    return [int(listen)] if listen.isdigit() and int(listen) in busy else []


def same_listener(rule: dict, other: dict) -> bool:
    """True when two rules would fight over the same listen port/address."""
    if (rule.get("listen"), rule.get("listen_ip") or "") != (other.get("listen"), other.get("listen_ip") or ""):
        return False
    left = rule.get("proto") or "tcp"
    right = other.get("proto") or "tcp"
    return left == right or "both" in (left, right)


def find_duplicate(rules: list[dict], rule: dict) -> dict | None:
    for existing in rules:
        if same_listener(rule, existing):
            return existing
    return None


def nft_text(data: dict) -> str:
    lines: list[str] = [
        "#!/usr/sbin/nft -f",
        "# generated by outlander - do not edit by hand",
        "table ip outlander",
        "delete table ip outlander",
        "table ip outlander {",
        "  chain prerouting {",
        "    type nat hook prerouting priority -110; policy accept;",
    ]
    hop = data.get("hop") or {}
    if hop.get("enabled"):
        lines.append(
            f"    {hop.get('proto', 'udp')} dport {hop['range']} dnat ip to {resolve_target(_target(hop['target']))}"
        )
    for raw in data.get("rules", []):
        rule = normalise_rule(raw)
        if effective_mode(rule) != "nft":
            continue
        protos = ["tcp", "udp"] if rule["proto"] == "both" else [rule["proto"]]
        for proto in protos:
            parts: list[str] = []
            if rule["listen_ip"]:
                parts.append(f"ip daddr {rule['listen_ip']}")
            if rule["listen"] == "*":
                parts.append(f"meta l4proto {proto}")
            else:
                parts.append(f"{proto} dport {rule['listen']}")
            parts.append(f"dnat ip to {resolve_target(rule['target'])}")
            lines.append("    " + " ".join(parts))
    lines += [
        "  }",
        "  chain postrouting {",
        "    type nat hook postrouting priority 100; policy accept;",
        "    ct status dnat masquerade",
        "  }",
        "  chain forward {",
        "    type filter hook forward priority 0; policy accept;",
        "    ct state established,related accept",
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def docker_allows(data: dict) -> list[str]:
    # accept everything our nat table DNATs, whatever the destination is
    commands: list[str] = [
        "iptables -C DOCKER-USER -m conntrack --ctstate DNAT -j ACCEPT 2>/dev/null || "
        "iptables -I DOCKER-USER 1 -m conntrack --ctstate DNAT -j ACCEPT 2>/dev/null || true"
    ]
    hop = data.get("hop") or {}
    if hop.get("enabled"):
        host, _, port = resolve_target(_target(hop["target"])).partition(":")
        if host.startswith("172.28.60.") and port:
            commands.append(_allow_command(host, port, hop.get("proto", "udp")))
    for raw in data.get("rules", []):
        rule = normalise_rule(raw)
        if effective_mode(rule) != "nft":
            continue
        host, _, port = resolve_target(rule["target"]).partition(":")
        if not host.startswith("172.28.60."):
            continue
        protos = ["tcp", "udp"] if rule["proto"] == "both" else [rule["proto"]]
        for proto in protos:
            commands.append(_allow_command(host, port, proto))
    return list(dict.fromkeys(commands))


def render(root: Path) -> Path:
    data = load(root)
    save(root, data)
    path = root / NFT_OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(nft_text(data), encoding="utf-8")
    allowance = root / "config/nftables/docker-allow.sh"
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    lines += docker_allows(data) or ["true"]
    allowance.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


normalise_target = _target
