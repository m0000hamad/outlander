"""Validated OpenVPN imports and isolated forwarding containers."""
from __future__ import annotations

import json
import os
import re
import shlex
import time
import secrets
from pathlib import Path


def sanitise(config: str, username: str = "", password: str = "") -> str:
    from .peers import PeerError
    if len(config) > 131072 or "\x00" in config:
        raise PeerError("OpenVPN profile is too large or invalid / کانفیگ نامعتبر است")
    blocks = {}
    def block(match):
        name, value = match.group(1), match.group(2)
        if name in blocks:
            raise PeerError("Duplicate OpenVPN credential block")
        blocks[name] = value.strip()
        return ""
    config = re.sub(r"<(ca|cert|key|tls-auth|tls-crypt)>\s*([\s\S]*?)</\1>", block, config)
    lines = []
    remotes = 0
    # Never allow arbitrary paths, scripts, plugins, management sockets, logging
    # destinations, host routes or credentials in command-line arguments.
    flags = {"client", "nobind", "persist-key", "persist-tun", "remote-random",
             "auth-nocache", "explicit-exit-notify"}
    values = {"proto", "resolv-retry", "remote-cert-tls", "verify-x509-name", "cipher",
              "data-ciphers", "data-ciphers-fallback", "auth", "tls-version-min",
              "key-direction", "connect-retry", "connect-timeout", "ping", "ping-restart",
              "reneg-sec", "tun-mtu", "mssfix", "verb"}
    ignored = {"dev", "dev-type", "route-nopull", "redirect-gateway", "route", "route-ipv6",
               "dhcp-option", "block-outside-dns", "register-dns", "setenv", "pull"}
    needs_auth = False
    for raw in config.splitlines():
        try:
            parts = shlex.split(raw, comments=True)
        except ValueError:
            raise PeerError("Invalid OpenVPN directive") from None
        if not parts:
            continue
        name, args = parts[0], parts[1:]
        if name in ignored:
            continue
        if name == "auth-user-pass" and not args:
            needs_auth = True
            continue
        if name == "remote":
            if not 1 <= len(args) <= 3 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", args[0]):
                raise PeerError("Invalid OpenVPN remote address")
            if len(args) > 1 and (not args[1].isdigit() or not 1 <= int(args[1]) <= 65535):
                raise PeerError("Invalid OpenVPN remote port")
            if len(args) > 2 and args[2] not in {"udp", "udp4", "tcp", "tcp4", "tcp-client", "tcp4-client"}:
                raise PeerError("Unsupported OpenVPN remote protocol")
            remotes += 1
        elif name in flags and not args:
            pass
        elif name in values and 1 <= len(args) <= 2 and all(re.fullmatch(r"[A-Za-z0-9_.:+/-]+", a) for a in args):
            if name == "proto" and args != ["udp"] and args != ["udp4"] and args != ["tcp-client"] and args != ["tcp4-client"]:
                raise PeerError("OpenVPN needs a client UDP/TCP protocol")
        else:
            raise PeerError("Unsupported OpenVPN directive: " + name[:40])
        lines.append(" ".join(parts))
    if not remotes or "ca" not in blocks or not (("cert" in blocks and "key" in blocks) or needs_auth):
        raise PeerError("Use a self-contained .ovpn with inline CA and client certificate/key or username/password / کانفیگ باید گواهی‌های داخلی داشته باشد")
    if "ENCRYPTED" in blocks.get("key", ""):
        raise PeerError("Encrypted client keys are not supported")
    if needs_auth and (not username or not password or any(c in username + password for c in "\r\n\x00")):
        raise PeerError("OpenVPN username and password are required / نام کاربری و رمز OpenVPN لازم است")
    # Enforce server authentication even if the imported profile omits it.
    minimum_tls = "1.3" if "tls-version-min 1.3" in lines else "1.2"
    lines += ["client", "dev tun0", "remote-cert-tls server", "route-nopull", "auth-nocache",
              "persist-tun", "persist-key", "tls-version-min " + minimum_tls,
              "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"]
    if needs_auth:
        lines.append("auth-user-pass /vpn/auth.txt")
    lines += [f"<{k}>\n{v}\n</{k}>" for k, v in blocks.items()]
    return "\n".join(lines) + "\n"


def import_profile(root: Path, form: dict) -> str:
    from . import peers
    name = str(form.get("name", "")).strip()
    if not name or len(name) > 80:
        raise peers.PeerError("OpenVPN profile name is required")
    username, password = str(form.get("vpn_username", "")), str(form.get("vpn_password", ""))
    config = sanitise(str(form.get("ovpn", "")), username, password)
    peer_id = secrets.token_hex(6)
    profile = {"status": "ready", "config": config, "auth": username + "\n" + password + "\n"}
    with peers._LOCK:
        data = peers.load(root)
        data["servers"].append({"id": peer_id, "name": name, "host": "OpenVPN", "username": "",
                                "ssh_port": 0, "imported": True, "status": "saved",
                                "transports": {"openvpn": profile}})
        peers._save(root, data)
    return peer_id


def create_forward(root: Path, rule: dict, peer: dict, probe_only: bool = False):
    from . import peers
    profile = peer["transports"]["openvpn"]
    folder = peers.directory(root) / "forwards" / rule["peer_rule_id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = peers.forward_config(rule, peer)
    if probe_only:
        config["inbounds"] = [{"protocol": "socks", "listen": "0.0.0.0", "port": int(rule["listen"]),
                               "settings": {"auth": "noauth", "udp": False}}]
    for filename, content in {"config.json": json.dumps(config), "client.ovpn": profile["config"],
                              "auth.txt": profile.get("auth", "")}.items():
        peers._write(folder / filename, content)
    status, raw = peers.api("GET", "/containers/" + os.environ.get("CONSOLE_CONTAINER", "ol-console") + "/json")
    if status != 200:
        raise peers.PeerError("Console image unavailable; rebuild console")
    image = json.loads(raw)["Image"]
    port = str(rule["listen"])
    protocols = ["tcp", "udp"] if rule["proto"] == "both" else [rule["proto"]]
    ports = {port + "/" + protocol: [{"HostIp": "127.0.0.1" if probe_only else "0.0.0.0", "HostPort": port}]
             for protocol in protocols}
    labels = {"outlander.peer.instance": peers.load(root)["instance"]}
    if probe_only:
        labels.update({"outlander.peer.probe": "true", "outlander.peer.probe_rule_id": rule["peer_rule_id"]})
    payload = {"Image": image, "User": "0:0", "WorkingDir": "/repo", "Labels": labels,
               "Cmd": ["python3", "/runtime.py"], "ExposedPorts": {p: {} for p in ports},
               "Healthcheck": {"Test": ["NONE"]},
               "HostConfig": {"NetworkMode": "bridge", "CapAdd": ["NET_ADMIN"],
                              "Sysctls": {"net.ipv4.conf.all.rp_filter": "2", "net.ipv4.conf.default.rp_filter": "2"},
                              "Devices": [{"PathOnHost": "/dev/net/tun", "PathInContainer": "/dev/net/tun", "CgroupPermissions": "rwm"}],
                              "PortBindings": ports, "RestartPolicy": {"Name": "no" if probe_only else "unless-stopped"},
                              "Binds": [peers._host_path(root, root / "console/openvpn_runtime.py") + ":/runtime.py:ro", peers._host_path(root, folder) + ":/vpn:ro"],
                              "LogConfig": {"Type": "json-file", "Config": {"max-size": "5m", "max-file": "2"}}}}
    name = peers.forward_name(rule)
    status, _ = peers.api("POST", "/containers/create?name=" + name, payload)
    if status != 201:
        raise peers.PeerError("Could not create OpenVPN forward; check TUN support and rebuild console")
    try:
        status, _ = peers.api("POST", f"/containers/{name}/start")
        if status not in {204, 304}:
            raise peers.PeerError("Could not start OpenVPN forward")
        # Verify the private listener, even for UDP-only rules, using container
        # exec below rather than the published host port (Docker proxies can bind early).
        deadline = time.monotonic() + 85
        while time.monotonic() < deadline:
            info = peers._owned(root, name)
            if not info or not info.get("State", {}).get("Running"):
                break
            status, raw = peers.api("POST", f"/containers/{name}/exec", {"Cmd": ["python3", "-c",
                "import pathlib; raise SystemExit(0 if pathlib.Path('/tmp/openvpn-ready').exists() else 1)"]})
            if status == 201:
                exec_id = json.loads(raw)["Id"]
                peers.api("POST", f"/exec/{exec_id}/start", {"Detach": False, "Tty": False})
                status, raw = peers.api("GET", f"/exec/{exec_id}/json")
                if status == 200 and json.loads(raw).get("ExitCode") == 0:
                    return
            time.sleep(.5)
        raise peers.PeerError("OpenVPN connection failed; check profile, credentials, server and firewall / اتصال OpenVPN ناموفق بود")
    except BaseException:
        peers.api("DELETE", f"/containers/{name}?force=1")
        raise
