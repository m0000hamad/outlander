"""Filesystem, docker and nftables operations behind the web console."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("OUTLANDER_ROOT", "/repo")).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from outlander import forward, panel, render  # noqa: E402
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
    rule = forward.normalise_rule(
        {"proto": proto, "listen_ip": listen_ip, "listen": listen, "target": target}
    )
    conflicts = forward.conflicting_ports(rule)
    if conflicts:
        busy = ", ".join(str(port) for port in conflicts)
        raise ValueError(
            f"پورت {busy} همین حالا روی سرور سرویس دارد؛ فوروارد کردنش آن سرویس را می‌خواباند. "
            "پورت دیگری بدهید یا IP ورودی را مشخص کنید."
        )
    for existing in data["rules"]:
        if existing.get("listen") == rule["listen"] and (existing.get("listen_ip") or "") == rule["listen_ip"] \
                and existing.get("proto") == rule["proto"]:
            raise ValueError("a rule for this port/ip already exists")
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
    hop["target"] = forward.normalise_target(target)
    if not forward.PORT_RE.match(hop["range"]):
        raise ValueError("range must look like 20000-40000")
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
        return True, "panel routed through the gateway via the 3.x API (" + ", ".join(
            str(tag) for tag in summary["outbounds"]
        ) + ")"

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
        return True, "panel routed through the gateway (" + ", ".join(summary["outbounds"]) + ")"
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        if stopped:
            try:
                container_action("panel", "start")
            except Exception:
                pass
        return False, f"{type(exc).__name__}: {exc}"
