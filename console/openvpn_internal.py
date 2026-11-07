"""An opt-in OpenVPN server on the console host, without external profiles or SSH."""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
from pathlib import Path

from . import openvpn, openvpn_server, peers
from outlander import forward, probe

KIND = "openvpn_internal"


def find(root: Path) -> dict | None:
    return next((peer for peer in peers.load(root)["servers"] if peer.get("internal")), None)


def ensure_peer(root: Path) -> str:
    with peers._LOCK:
        existing = find(root)
        if existing:
            return existing["id"]
        data = peers.load(root)
        peer_id = secrets.token_hex(6)
        data["servers"].append({"id": peer_id, "name": "Internal OpenVPN", "host": "this-server",
                                "username": "", "ssh_port": 0, "internal": True,
                                "status": "saved", "transports": {}})
        peers._save(root, data)
        return peer_id


def names(root: Path) -> tuple[str, str]:
    instance = peers.load(root)["instance"]
    return f"ol-openvpn-{instance}", f"ol-peer-{instance}-internal-openvpn"


def status(root: Path) -> dict:
    peer = find(root)
    if not peer:
        return {"state": "disabled", "enabled": False}
    profile = peer.get("transports", {}).get(KIND, {})
    result = {"state": "disabled", "enabled": bool(profile.get("enabled")), "message": peer.get("message", "")}
    if peer.get("status") == "working":
        return result | {"state": "working"}
    if profile.get("status") == "ready":
        try:
            info = peers._owned(root, names(root)[1])
            running = info and info.get("State", {}).get("Running")
            unhealthy = (info or {}).get("State", {}).get("Health", {}).get("Status") == "unhealthy"
            result["state"] = "ready" if running and not unhealthy else "stopped"
        except Exception:
            result["state"] = "stopped"
    if peer.get("status") == "error":
        if result["state"] == "ready":
            result["warning"] = True
        else:
            result["state"] = "error"
    return result


def forward_form(root: Path, form: dict) -> dict:
    peer = find(root)
    if not peer or status(root)["state"] != "ready":
        raise peers.PeerError("Enable Internal OpenVPN and wait until ready / ابتدا OpenVPN داخلی را فعال کنید و منتظر آماده‌شدن بمانید")
    return form | {"mode": "peer", "peer_id": peer["id"], "transport": KIND}


def _ensure_network(root: Path, name: str) -> None:
    instance = peers.load(root)["instance"]
    code, raw = peers.api("GET", "/networks/" + name)
    if code == 200:
        if json.loads(raw).get("Labels", {}).get("outlander.peer.instance") != instance:
            raise peers.PeerError("Internal VPN network belongs to another installation")
        return
    if code != 404:
        raise peers.PeerError("Could not inspect Internal OpenVPN network")
    code, _ = peers.api("POST", "/networks/create", {"Name": name, "Driver": "bridge",
        "Labels": {"outlander.peer.instance": instance, "outlander.peer.role": KIND}})
    if code != 201:
        raise peers.PeerError("Could not create Internal OpenVPN network")


def server_config(profile: dict) -> str:
    network = ipaddress.ip_network(profile["subnet"])
    c = profile["credentials"]
    return (f"port 1194\nproto udp4\ndev tun0\ntopology subnet\nserver {network.network_address} 255.255.255.0\n"
            "dh none\nremote-cert-tls client\ntls-version-min 1.2\n"
            "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305\n"
            "keepalive 10 60\npersist-key\npersist-tun\nduplicate-cn\nverb 2\n"
            + "".join(f"<{tag}>\n{c[name]}</{tag}>\n" for tag, name in
                      (("ca", "ca.crt"), ("cert", "server.crt"), ("key", "server.key"), ("tls-crypt", "tls.key"))))


def provision(root: Path, peer_id: str) -> None:
    peer = peers.get(root, peer_id)
    if not peer.get("internal"):
        raise peers.PeerError("Internal OpenVPN entry required")
    network, name = names(root)
    profile = peer.get("transports", {}).get(KIND, {})
    profile.update(status="installing", enabled=True)
    peers._profile(root, peer_id, KIND, profile)
    if not profile.get("credentials"):
        profile.update(credentials=openvpn_server.credentials(),
                       subnet=f"10.{secrets.randbelow(200)+20}.{secrets.randbelow(254)+1}.0/24")
    profile.update(status="installing", enabled=True, network=network, port=1194)
    profile["config"] = openvpn_server.client_config({"host": name}, profile)
    peers._profile(root, peer_id, KIND, profile)
    _ensure_network(root, network)
    folder = peers.directory(root) / peer_id / KIND
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    peers._write(folder / "server.ovpn", server_config(profile))
    vpn_ip = str(ipaddress.ip_network(profile["subnet"]).network_address + 1)
    peers._write(folder / "server.json", json.dumps({"subnet": profile["subnet"], "vpn_ip": vpn_ip}))
    created = False
    if not peers._owned(root, name):
        code, raw = peers.api("GET", "/containers/" + os.environ.get("CONSOLE_CONTAINER", "ol-console") + "/json")
        if code != 200:
            raise peers.PeerError("Console image unavailable; rebuild console")
        image = json.loads(raw)["Image"]
        payload = {"Image": image, "User": "0:0", "Cmd": ["python3", "/runtime.py", "server"],
            "Labels": {"outlander.peer.instance": peers.load(root)["instance"], "outlander.peer.role": KIND},
            "Healthcheck": {"Test": ["CMD", "python3", "-c", "from pathlib import Path; raise SystemExit(0 if Path('/tmp/openvpn-server-ready').exists() else 1)"],
                            "Interval": 5000000000, "Timeout": 2000000000, "Retries": 3, "StartPeriod": 15000000000},
            "HostConfig": {"NetworkMode": network, "CapAdd": ["NET_ADMIN"],
                "Sysctls": {"net.ipv4.ip_forward": "1", "net.ipv4.conf.all.rp_filter": "2", "net.ipv4.conf.default.rp_filter": "2"},
                "Devices": [{"PathOnHost": "/dev/net/tun", "PathInContainer": "/dev/net/tun", "CgroupPermissions": "rwm"}],
                "RestartPolicy": {"Name": "unless-stopped"},
                "Binds": [peers._host_path(root, folder) + ":/vpn:ro",
                          peers._host_path(root, root / "console/openvpn_runtime.py") + ":/runtime.py:ro"],
                "LogConfig": {"Type": "json-file", "Config": {"max-size": "5m", "max-file": "2"}}}}
        code, _ = peers.api("POST", "/containers/create?name=" + name, payload)
        if code != 201:
            raise peers.PeerError("Could not install Internal OpenVPN; check TUN support / نصب OpenVPN داخلی ناموفق بود")
        created = True
    try:
        code, _ = peers.api("POST", f"/containers/{name}/start")
        if code not in {204, 304}:
            raise peers.PeerError("Could not start Internal OpenVPN")
        openvpn.wait_ready(root, name, "/tmp/openvpn-server-ready", 30)
        # A private HTTP response reached through SOCKS -> Xray -> TLS VPN
        # proves both authentication and actual data transport before saying ready.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            listen = sock.getsockname()[1]
        rule = forward.normalise_rule({"mode": "peer", "transport": KIND, "peer_id": peer_id,
            "peer_rule_id": secrets.token_hex(6), "listen": str(listen), "proto": "tcp", "target": f"{vpn_ip}:38080"})
        probe_created = False
        try:
            openvpn.create_forward(root, rule, peers.get(root, peer_id), probe_only=True)
            probe_created = True
            if not probe.socks5_http_reachable("127.0.0.1", listen, f"http://{vpn_ip}:38080/health", 10):
                raise peers.PeerError("Internal OpenVPN traffic check failed / بررسی ترافیک OpenVPN داخلی ناموفق بود")
        finally:
            if probe_created:
                peers.delete_forward(root, rule)
        profile.update(status="ready", message="Internal OpenVPN ready / OpenVPN داخلی آماده است")
        peers._profile(root, peer_id, KIND, profile)
    except BaseException:
        if created:
            peers.api("DELETE", f"/containers/{name}?force=1")
        raise


def disable(root: Path, peer_id: str) -> None:
    from . import ops
    # Serialize with forward creation/edit/removal so a new rule cannot race
    # disabling its server after the dependency check.
    with ops._FORWARD_LOCK:
        peer = peers.get(root, peer_id)
        if not peer.get("internal"):
            raise peers.PeerError("Internal OpenVPN entry required")
        if any(rule.get("peer_id") == peer_id for rule in forward.load(root)["rules"]):
            raise peers.PeerError("Remove Internal OpenVPN forwards before disabling / ابتدا فورواردهای OpenVPN داخلی را حذف کنید")
        name = names(root)[1]
        if peers._owned(root, name):
            code, _ = peers.api("DELETE", f"/containers/{name}?force=1")
            if code not in {204, 404}:
                raise peers.PeerError("Could not disable Internal OpenVPN")
        profile = peer.get("transports", {}).get(KIND, {})
        peers._profile(root, peer_id, KIND, profile | {"enabled": False, "status": "disabled", "message": "Disabled / غیرفعال"})
