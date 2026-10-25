"""Filesystem, docker and nftables operations behind the web console."""

from __future__ import annotations

import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("OUTLANDER_ROOT", "/repo")).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from outlander import certs, forward, inbounds, panel, render  # noqa: E402
from outlander.uri import LinkError, parse  # noqa: E402

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
STATE_FILE = Path(os.environ.get("CONSOLE_STATE", "/data/state.json"))
HOST_UID = int(os.environ.get("HOST_UID", "0") or 0)
HOST_GID = int(os.environ.get("HOST_GID", "0") or 0)

SERVICES = {
    "gateway": "ol-gateway",
    "panel": "ol-panel",
    "singbox": "ol-singbox",
    "console": "ol-console",
}


class DockerUnavailable(RuntimeError):
    pass


class OpError(RuntimeError):
    """A user facing error; ``code`` is an i18n key and ``params`` its placeholders."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 25):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def _docker(method: str, path: str) -> tuple[int, bytes]:
    if not Path(DOCKER_SOCK).exists():
        raise DockerUnavailable("docker socket is not mounted")
    connection = _UnixConnection(DOCKER_SOCK)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    except OSError as exc:
        raise DockerUnavailable(str(exc)) from exc
    finally:
        connection.close()


def container_state(key: str) -> dict:
    name = SERVICES.get(key, key)
    try:
        status, body = _docker("GET", f"/containers/{name}/json")
    except DockerUnavailable as exc:
        return {"state": "unknown", "detail": str(exc), "restarts": 0}
    if status == 404:
        return {"state": "missing", "detail": "container not created", "restarts": 0}
    if status != 200:
        return {"state": "unknown", "detail": f"http {status}", "restarts": 0}
    payload = json.loads(body or b"{}")
    state = payload.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    return {
        "state": state.get("Status") or "unknown",
        "detail": health or state.get("Status") or "unknown",
        "restarts": payload.get("RestartCount", 0),
    }


def container_action(key: str, action: str) -> None:
    verbs = {"restart": f"/containers/{SERVICES[key]}/restart?t=10", "start": f"/containers/{SERVICES[key]}/start", "stop": f"/containers/{SERVICES[key]}/stop?t=10"}
    if action not in verbs:
        raise ValueError(f"unknown action {action}")
    status, body = _docker("POST", verbs[action])
    if status >= 300:
        raise DockerUnavailable(body.decode("utf-8", "replace").strip() or f"http {status}")


def _own(paths: list[Path]) -> None:
    if HOST_UID <= 0:
        return
    for path in paths:
        try:
            os.chown(path, HOST_UID, HOST_GID)
        except OSError:
            pass


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def remember(key: str, message: str) -> None:
    state = read_state()
    state[key] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def inventory() -> dict:
    return render.load(ROOT)


def env() -> dict:
    return render.read_env(ROOT)


def forwarding() -> dict:
    return forward.load(ROOT)


def render_all() -> None:
    paths = render.render(ROOT)
    nft = forward.render(ROOT)
    _own(
        [
            *paths.values(),
            nft,
            ROOT / "config/nftables/docker-allow.sh",
            ROOT / "config/forwarding.json",
        ]
    )


def add_links(text: str, tag: str, group: str, resolve: str, via: str) -> tuple[int, list[str]]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return 0, ["no links provided"]

    parsed: list[tuple[str, object]] = []
    errors: list[str] = []
    for line in lines:
        try:
            parsed.append((line, parse(line)))
        except LinkError as exc:
            preview = line if len(line) <= 40 else line[:40] + "..."
            errors.append(f"{preview} -> {exc}")
    if errors:
        return 0, errors

    data = render.load(ROOT)
    if via and not render.find(data, via):
        return 0, [f"via target '{via}' does not exist"]
    existing = {channel["tag"] for channel in data["channels"]}

    added: list[str] = []
    for index, (line, link) in enumerate(parsed, start=1):
        base = tag.strip() if (tag and len(parsed) == 1) else (link.name or link.address)
        base = render.slug(base)
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        try:
            render.add_channel(data, line, tag=candidate, group=group or "cdn", resolve=resolve, via=via)
        except LinkError as exc:
            errors.append(str(exc))
            continue
        existing.add(candidate)
        added.append(candidate)

    if added:
        render.save(ROOT, data)
    return len(added), errors


def remove_channel(tag: str) -> bool:
    data = render.load(ROOT)
    channel = render.find(data, tag)
    if not channel:
        return False
    data["channels"].remove(channel)
    for other in data["channels"]:
        if other.get("via") == tag:
            other["via"] = ""
    render.save(ROOT, data)
    return True


def toggle_channel(tag: str) -> bool | None:
    data = render.load(ROOT)
    channel = render.find(data, tag)
    if not channel:
        return None
    channel["enabled"] = not channel.get("enabled", True)
    render.save(ROOT, data)
    return bool(channel["enabled"])


def add_forward(proto: str, listen_ip: str, listen: str, target: str) -> str:
    data = forward.load(ROOT)
    try:
        rule = forward.normalise_rule(
            {"proto": proto, "listen_ip": listen_ip, "listen": listen, "target": target}
        )
    except ValueError as exc:
        raise OpError("err.invalid_rule", detail=str(exc)) from exc
    conflicts = forward.conflicting_ports(rule)
    if conflicts:
        ports = ", ".join(str(port) for port in conflicts)
        owner = forward.listener_owner(conflicts[0])
        if owner:
            raise OpError("err.forward_busy", ports=ports, owner=owner)
        raise OpError("err.forward_busy_unknown", ports=ports)
    for existing in data["rules"]:
        if forward.same_listener(existing, rule):
            raise OpError("err.forward_duplicate", listen=rule["listen"])
    data["rules"].append(rule)
    forward.save(ROOT, data)
    return f"{rule['proto']}/{rule['listen']} -> {rule['target']}"


def remove_forward(index: int) -> bool:
    data = forward.load(ROOT)
    if not 0 <= index < len(data["rules"]):
        return False
    data["rules"].pop(index)
    forward.save(ROOT, data)
    return True


def save_hop(enabled: bool, proto: str, port_range: str, target: str) -> None:
    data = forward.load(ROOT)
    hop = data.get("hop") or {}
    hop["enabled"] = enabled
    hop["proto"] = proto if proto in {"udp", "tcp"} else "udp"
    hop["range"] = port_range
    try:
        hop["target"] = forward.normalise_target(target)
    except ValueError as exc:
        raise OpError("err.invalid_rule", detail=str(exc)) from exc
    if not forward.PORT_RE.match(hop["range"]):
        raise OpError("err.hop_range")
    data["hop"] = hop
    forward.save(ROOT, data)


def apply_forwarding() -> tuple[bool, str]:
    nft_file = ROOT / "config/nftables/outlander.nft"
    if not nft_file.exists():
        return False, "forwarding rules were not rendered yet"
    if not shutil.which("nft"):
        return False, "nft is not available in the console container"
    result = subprocess.run(["nft", "-f", str(nft_file)], capture_output=True, text=True)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    allow = ROOT / "config/nftables/docker-allow.sh"
    if allow.exists():
        subprocess.run(["bash", str(allow)], capture_output=True, text=True)
    check = subprocess.run(["nft", "list", "table", "ip", "outlander"], capture_output=True, text=True)
    if check.returncode != 0:
        return False, check.stderr.strip()
    return True, "firewall rules applied"


def _reserved_ports() -> set[int]:
    env = render.read_env(ROOT)
    reserved: set[int] = set()
    for key in ("PANEL_PORT", "CONSOLE_PORT", "GATEWAY_SOCKS_PORT", "GATEWAY_HTTP_PORT", "HY2_PORT", "TUIC_PORT"):
        value = str(env.get(key) or "").strip()
        if value.isdigit():
            reserved.add(int(value))
    return reserved


def panel_client() -> panel.PanelClient:
    env = render.read_env(ROOT)
    base = f"http://127.0.0.1:{env.get('PANEL_PORT', '2053')}"
    client = panel.PanelClient(base, env.get("PANEL_USERNAME", "admin"), env.get("PANEL_PASSWORD", ""))
    error: Exception | None = None
    for _ in range(3):
        try:
            return client.login()
        except panel.PanelError as exc:
            error = exc
            time.sleep(1.5)
    raise OpError("err.proxy_panel", detail=str(error))


def proxy_ports(public_host: str) -> list[dict]:
    rows: list[dict] = []
    for inbound in panel_client().list_inbounds():
        link = inbounds.link_for(inbound, public_host)
        if not link:
            continue
        stream = inbound.get("streamSettings") or {}
        rows.append(
            {
                "id": inbound.get("id"),
                "remark": inbound.get("remark") or "",
                "port": inbound.get("port"),
                "security": (stream.get("security") or "none"),
                "link": link,
            }
        )
    return rows


def add_proxy_port(
    port: int,
    remark: str,
    kind: str,
    public_host: str,
    domain: str = "",
    email: str = "",
    want_cert: bool = False,
) -> dict:
    if not 0 < port < 65536:
        raise OpError("err.proxy_busy", ports=str(port))
    if port in _reserved_ports() or forward.conflicting_ports({"listen": str(port), "listen_ip": ""}):
        raise OpError("err.proxy_busy", ports=str(port))
    for rule in forward.load(ROOT).get("rules", []):
        listen = str(rule.get("listen") or "")
        if listen == str(port):
            raise OpError("err.proxy_forwarded", port=str(port))
        if "-" in listen:
            low, _, high = listen.partition("-")
            if low.isdigit() and high.isdigit() and int(low) <= port <= int(high):
                raise OpError("err.proxy_forwarded", port=str(port))

    domain = certs.normalise_domain(domain)
    token = cloudflare_settings()["token"]
    if want_cert:
        if not certs.valid_domain(domain):
            raise OpError("err.cert_domain")
        if not token:
            raise OpError("err.cert_token")

    client = panel_client()
    try:
        existing = {int(item.get("port") or 0) for item in client.list_inbounds()}
        if port in existing:
            raise OpError("err.proxy_exists", port=str(port))
        uuid_value = client.new_uuid()
        name = remark.strip() or (f"tls-{domain}" if want_cert else f"proxy-{port}")
        if want_cert:
            issue_certificate(domain, email, token)
            cert_file, key_file = certs.panel_cert_paths(domain)
            payload = inbounds.ws_tls_inbound(port, name, uuid_value, domain, cert_file, key_file)
            link = inbounds.ws_tls_link(domain, port, uuid_value, domain, remark=name)
        elif kind == "reality":
            private, public = client.new_reality_keys()
            short_id = secrets.token_hex(8)
            payload = inbounds.reality_inbound(port, name, uuid_value, private, public, short_id)
            link = inbounds.reality_link(public_host, port, uuid_value, public, short_id, remark=name)
        else:
            payload = inbounds.ws_inbound(port, name, uuid_value)
            link = inbounds.ws_link(public_host, port, uuid_value, remark=name)
        client.add_inbound(payload)
    except panel.PanelError as exc:
        raise OpError("err.proxy_create", detail=str(exc)) from exc
    return {"link": link, "remark": name, "port": port, "domain": domain if want_cert else ""}


def delete_proxy_port(inbound_id: int) -> None:
    client = panel_client()
    target = next((item for item in client.list_inbounds() if item.get("id") == inbound_id), None)
    if not target or not inbounds.link_for(target, "0.0.0.0"):
        raise OpError("err.proxy_not_found")
    try:
        client.delete_inbound(inbound_id)
    except panel.PanelError as exc:
        raise OpError("err.proxy_create", detail=str(exc)) from exc


def save_env(updates: dict[str, str]) -> None:
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cloudflare_settings() -> dict:
    env = render.read_env(ROOT)
    return {
        "token": (env.get("CF_API_TOKEN") or "").strip(),
        "email": (env.get("CF_EMAIL") or "").strip(),
    }


def _certbot(args: list[str]) -> tuple[bool, str]:
    if not shutil.which("certbot"):
        return False, "certbot is not installed in the console image"
    command = [
        "certbot",
        *args,
        "--config-dir",
        str(ROOT / "data/letsencrypt"),
        "--work-dir",
        str(ROOT / "data/letsencrypt-work"),
        "--logs-dir",
        str(ROOT / "data/letsencrypt-logs"),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "certbot timed out"
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return False, " ".join(lines[-3:])[:400]
    return True, ""


def _install_certificate(domain: str) -> None:
    live = ROOT / "data/letsencrypt" / "live" / domain
    target = certs.host_cert_dir(ROOT, domain)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("fullchain.pem", "privkey.pem"):
        source = live / name
        if not source.exists():
            raise OpError("err.cert_failed", detail=f"missing {name} for {domain}")
        shutil.copy2(source, target / name)
        os.chmod(target / name, 0o644)
    _own([target / "fullchain.pem", target / "privkey.pem"])


def install_all_certificates() -> int:
    live_root = ROOT / "data/letsencrypt" / "live"
    count = 0
    if live_root.exists():
        for lineage in sorted(live_root.iterdir()):
            if (lineage / "fullchain.pem").exists() and (lineage / "privkey.pem").exists():
                _install_certificate(lineage.name)
                count += 1
    if count:
        try:
            container_action("panel", "restart")
        except Exception:  # noqa: BLE001 - docker may be unavailable
            pass
    return count


def issue_certificate(domain: str, email: str, token: str) -> None:
    domain = certs.normalise_domain(domain)
    if not certs.valid_domain(domain):
        raise OpError("err.cert_domain")
    if not token:
        raise OpError("err.cert_token")
    credentials = ROOT / "data/cloudflare.ini"
    credentials.parent.mkdir(parents=True, exist_ok=True)
    credentials.write_text(f"dns_cloudflare_api_token = {token}\n", encoding="utf-8")
    os.chmod(credentials, 0o600)
    ok, detail = _certbot(
        [
            "certonly",
            "--dns-cloudflare",
            "--dns-cloudflare-credentials",
            str(credentials),
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
            "--email",
            email or f"admin@{domain}",
            "-d",
            domain,
        ]
    )
    if not ok:
        raise OpError("err.cert_failed", detail=detail)
    _install_certificate(domain)


def renew_certificates() -> tuple[bool, str]:
    credentials = ROOT / "data/cloudflare.ini"
    if not credentials.exists():
        return False, "no Cloudflare credentials stored yet"
    ok, detail = _certbot(
        [
            "renew",
            "--dns-cloudflare",
            "--dns-cloudflare-credentials",
            str(credentials),
            "--non-interactive",
            "--quiet",
        ]
    )
    if not ok:
        return False, detail
    count = install_all_certificates()
    return True, f"{count} certificate(s) refreshed"


def patch_panel_route() -> tuple[bool, str]:
    db = ROOT / "data/x-ui/x-ui.db"
    data = render.load(ROOT)
    env = render.read_env(ROOT)
    outbound, rule = render.panel_fragments(data, env)

    if not panel.has_legacy_template(db):
        base = f"http://127.0.0.1:{env.get('PANEL_PORT', '2053')}"
        try:
            summary = panel.patch_api(
                base,
                env.get("PANEL_USERNAME", "admin"),
                env.get("PANEL_PASSWORD", ""),
                outbound,
                rule,
            )
        except panel.PanelError as exc:
            return False, str(exc)
        return True, ", ".join(str(tag) for tag in summary["outbounds"])

    if not db.exists():
        return False, "panel database not found - open the panel once first"
    running = container_state("panel").get("state") == "running"
    stopped = False
    try:
        if running:
            container_action("panel", "stop")
            stopped = True
        panel.backup(db)
        summary = panel.patch_database(db, outbound, rule)
        if stopped:
            container_action("panel", "start")
        return True, ", ".join(summary["outbounds"])
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        if stopped:
            try:
                container_action("panel", "start")
            except Exception:
                pass
        return False, f"{type(exc).__name__}: {exc}"
