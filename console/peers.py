"""Isolated, opt-in server-to-server forwarding; never manages legacy containers."""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

from outlander import forward

KINDS = {"ssh": "SSH / TCP", "reality": "VLESS + REALITY", "xhttp": "VLESS + XHTTP + REALITY"}
IMAGE = "ghcr.io/xtls/xray-core:latest"
_LOCK = threading.RLock()
_JOBS: set[str] = set()
_SSH: dict[str, subprocess.Popen] = {}


class PeerError(ValueError):
    pass


def directory(root: Path) -> Path:
    path = root / "data/peers"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with private permissions before writing any credential bytes.
    fd = os.open(str(path) + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.chmod(str(path) + ".tmp", 0o600)
    os.replace(str(path) + ".tmp", path)


def load(root: Path) -> dict:
    with _LOCK:
        path = directory(root) / "servers.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        data = {"instance": secrets.token_hex(6), "servers": []}
        _save(root, data)
        return data


def _save(root: Path, data: dict) -> None:
    _write(directory(root) / "servers.json", json.dumps(data, ensure_ascii=False, indent=2))


def get(root: Path, peer_id: str) -> dict:
    for peer in load(root)["servers"]:
        if peer["id"] == peer_id:
            return peer
    raise PeerError("Server not found / سرور پیدا نشد")


def public(root: Path) -> list[dict]:
    return [{key: value for key, value in peer.items() if key not in {"password", "private_key"}}
            | {"transports": {kind: {k: v for k, v in profile.items()
                                    if k in {"status", "port", "sni", "message"}}
                              for kind, profile in peer.get("transports", {}).items()}}
            for peer in load(root)["servers"]]


def save_server(root: Path, form: dict) -> str:
    host = str(form.get("host", "")).strip()
    username = str(form.get("username", "root")).strip()
    name = str(form.get("name", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", host):
        raise PeerError("Invalid server address / آدرس سرور نامعتبر است")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise PeerError("Invalid SSH user / کاربر SSH نامعتبر است")
    port = int(form.get("ssh_port") or 22)
    if not 1 <= port <= 65535 or not name or len(name) > 80:
        raise PeerError("Check name and SSH port / نام و پورت SSH را بررسی کنید")
    password = str(form.get("password") or "")
    if len(password) > 4096 or "\n" in password or "\r" in password:
        raise PeerError("Invalid SSH password / پسورد SSH نامعتبر است")
    key = str(form.get("private_key") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if bool(password) == bool(key):
        raise PeerError("Provide one password OR private key / فقط پسورد یا کلید خصوصی را وارد کنید")
    if key and (len(key) > 32768 or not key.startswith("-----BEGIN ") or "PRIVATE KEY-----" not in key):
        raise PeerError("Invalid private key / کلید خصوصی معتبر نیست")
    with _LOCK:
        data = load(root)
        peer_id = secrets.token_hex(6)
        data["servers"].append({"id": peer_id, "name": name, "host": host, "username": username,
                                "ssh_port": port, "password": password, "private_key": key,
                                "status": "saved", "fingerprint": "", "transports": {}})
        _save(root, data)
        if key:
            _write(directory(root) / peer_id / "identity", key + "\n")
        return peer_id


def _update(root: Path, peer_id: str, **fields) -> None:
    with _LOCK:
        data = load(root)
        peer = next(p for p in data["servers"] if p["id"] == peer_id)
        peer.update(fields)
        _save(root, data)


def _ssh_command(root: Path, peer: dict, extra: list[str] | None = None) -> list[str]:
    folder = directory(root) / peer["id"]
    folder.mkdir(mode=0o700, exist_ok=True)
    args = ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
            "-o", "IPQoS=none", "-o", "UserKnownHostsFile=" + str(folder / "known_hosts"),
            "-p", str(peer["ssh_port"])]
    if peer.get("private_key"):
        args += ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-i", str(folder / "identity")]
    else:
        args += ["-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password,keyboard-interactive"]
    args += extra or []
    args += [peer["username"] + "@" + peer["host"]]
    return args


def _spawn(root: Path, peer: dict, extra: list[str], command: str | None = None) -> subprocess.Popen:
    args = _ssh_command(root, peer, extra)
    if command is not None:
        args.append(command)
    read_fd = write_fd = None
    try:
        if peer.get("password"):
            read_fd, write_fd = os.pipe()
            os.write(write_fd, (peer["password"] + "\n").encode())
            os.close(write_fd)
            write_fd = None
            args = ["sshpass", "-d", str(read_fd), *args]
        return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                pass_fds=(read_fd,) if read_fd is not None else ())
    except OSError as exc:
        raise PeerError("SSH tools unavailable; rebuild console image / ایمیج کنسول را بازسازی کنید") from exc
    finally:
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)


def run_ssh(root: Path, peer: dict, script: str, timeout: int = 30) -> str:
    process = _spawn(root, peer, [], "bash -se")
    try:
        stdout, stderr = process.communicate(script.encode(), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise PeerError("SSH operation timed out; retry / مهلت اتصال تمام شد؛ دوباره تلاش کنید") from None
    if process.returncode:
        # Never return remote stdout/stderr: either could contain credentials.
        if b"REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
            raise PeerError("SSH host key changed; verify server identity / کلید میزبان تغییر کرده است")
        marker = re.search(rb"OUTLANDER_ERROR:([a-z_]+)", stderr)
        code = marker.group(1).decode() if marker else "connection_or_remote_command"
        raise PeerError(f"SSH failed ({code}); check login, root access and server connectivity / اتصال یا اجرای فرمان ناموفق بود")
    return stdout.decode("utf-8", "replace").strip()


def test_server(root: Path, peer_id: str) -> None:
    peer = get(root, peer_id)
    result = run_ssh(root, peer, "test \"$(id -u)\" = 0 || { echo OUTLANDER_ERROR:root_required >&2; exit 1; }; echo OUTLANDER_OK\n")
    if result != "OUTLANDER_OK":
        raise PeerError("Unexpected SSH response / پاسخ SSH غیرمنتظره است")
    known = directory(root) / peer_id / "known_hosts"
    result = subprocess.run(["ssh-keygen", "-lf", str(known)], capture_output=True, text=True, timeout=5)
    fingerprints = re.findall(r"SHA256:[A-Za-z0-9+/]+", result.stdout)
    _update(root, peer_id, status="connected", fingerprint=", ".join(fingerprints))


def _profile(root: Path, peer_id: str, kind: str, profile: dict) -> None:
    with _LOCK:
        data = load(root)
        peer = next(p for p in data["servers"] if p["id"] == peer_id)
        peer.setdefault("transports", {})[kind] = profile
        _save(root, data)


def _keys() -> tuple[str, str]:
    private = subprocess.run(["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"],
                             capture_output=True, check=True, timeout=5).stdout
    public_key = subprocess.run(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"],
                                input=private, capture_output=True, check=True, timeout=5).stdout
    encode = lambda raw: base64.urlsafe_b64encode(raw[-32:]).decode().rstrip("=")
    return encode(private), encode(public_key)


def stream(profile: dict, kind: str, server: bool = False) -> dict:
    reality = ({"target": profile["sni"] + ":443", "serverNames": [profile["sni"]],
                "privateKey": profile["private_key"], "shortIds": [profile["short_id"]], "xver": 0}
               if server else {"serverName": profile["sni"], "password": profile["public_key"],
                               "shortId": profile["short_id"], "fingerprint": "chrome"})
    result = {"network": "xhttp" if kind == "xhttp" else "raw", "security": "reality",
              "realitySettings": reality, "sockopt": {"tcpMaxSeg": 1200}}
    if kind == "xhttp":
        result["xhttpSettings"] = {"path": profile["path"], "mode": "auto"}
    return result


def server_config(profile: dict, kind: str) -> dict:
    user = {"id": profile["uuid"]}
    if kind == "reality":
        user["flow"] = "xtls-rprx-vision"
    return {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "0.0.0.0", "port": profile["port"],
            "protocol": "vless", "settings": {"clients": [user], "decryption": "none"},
            "streamSettings": stream(profile, kind, True)}], "outbounds": [{"protocol": "freedom", "settings": {
                # A managed port tunnel intentionally reaches the peer's private
                # services. New Xray versions block these unless explicitly allowed.
                # Link-local/metadata networks remain blocked by the core default.
                "finalRules": [{"action": "allow", "ip": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                                                           "192.168.0.0/16", "::1/128", "fc00::/7"]}]
            }}]}


def provision_script(instance: str, peer_id: str, profile: dict, kind: str) -> str:
    name = f"ol-peer-{instance}-{peer_id}-{kind}"
    folder = f"/etc/outlander-peers/{instance}/{peer_id}/{kind}"
    encoded = base64.b64encode(json.dumps(server_config(profile, kind)).encode()).decode()
    # Names/ports are generated or validated; configuration crosses the shell only as base64.
    return f'''set -eu
test "$(id -u)" = 0 || {{ echo OUTLANDER_ERROR:root_required >&2; exit 1; }}
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y docker.io
  systemctl enable --now docker
fi
docker image inspect {IMAGE} >/dev/null 2>&1 || docker pull {IMAGE}
if docker inspect {name} >/dev/null 2>&1; then
  owner=$(docker inspect -f '{{{{ index .Config.Labels "outlander.peer.instance" }}}}' {name})
  test "$owner" = {instance} || {{ echo OUTLANDER_ERROR:not_owned >&2; exit 1; }}
  echo OUTLANDER_ALREADY_EXISTS
  exit 0
fi
if ss -H -lnt "sport = :{profile['port']}" | grep -q .; then
  echo OUTLANDER_ERROR:port_busy >&2; exit 1
fi
umask 077
mkdir -p {folder}
echo {encoded} | base64 -d > {folder}/candidate.json
# The official Xray image runs as uid/gid 65532. Share only this mounted
# configuration directory with that group; all SSH credentials remain 0600.
chown 0:65532 {folder} {folder}/candidate.json
chmod 750 {folder}
chmod 640 {folder}/candidate.json
docker run --rm -v {folder}:/etc/xray:ro {IMAGE} -test -c /etc/xray/candidate.json
mv {folder}/candidate.json {folder}/config.json
docker run -d --name {name} --label outlander.peer.instance={instance} --network host \\
  --restart unless-stopped --log-opt max-size=5m --log-opt max-file=2 \\
  -v {folder}:/etc/xray:ro {IMAGE} run -c /etc/xray/config.json
sleep 1
test "$(docker inspect -f '{{{{.State.Running}}}}' {name})" = true || {{ echo OUTLANDER_ERROR:container_failed >&2; exit 1; }}
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
  ufw allow {profile['port']}/tcp comment {name}
fi
echo OUTLANDER_READY
'''


def provision(root: Path, peer_id: str, kind: str, port: str = "", sni: str = "") -> None:
    if kind not in KINDS:
        raise PeerError("Unknown tunnel / نوع تونل نامعتبر است")
    test_server(root, peer_id)
    peer = get(root, peer_id)
    previous = peer.get("transports", {}).get(kind, {})
    if previous.get("status") == "ready":
        return  # Never replace or restart a working remote tunnel.
    if kind == "ssh":
        _profile(root, peer_id, kind, {"status": "ready", "message": "SSH ready / آماده"})
        return
    remote_port = int(port or (24443 if kind == "reality" else 24444))
    sni = sni.strip() or "www.microsoft.com"
    if not 1 <= remote_port <= 65535 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", sni):
        raise PeerError("Invalid tunnel port or SNI / پورت تونل یا SNI نامعتبر است")
    # Preserve pending credentials on retry after a network interruption.
    if previous.get("uuid"):
        profile = previous | {"status": "installing", "message": "Installing / در حال نصب"}
    else:
        private, public_key = _keys()
        profile = {"status": "installing", "port": remote_port, "sni": sni, "private_key": private,
                   "public_key": public_key, "uuid": str(uuid.uuid4()), "short_id": secrets.token_hex(8),
                   "path": "/" + secrets.token_hex(12), "message": "Installing / در حال نصب"}
    _profile(root, peer_id, kind, profile)
    result = run_ssh(root, peer, provision_script(load(root)["instance"], peer_id, profile, kind), timeout=240)
    if "OUTLANDER_READY" not in result and "OUTLANDER_ALREADY_EXISTS" not in result:
        raise PeerError("Remote setup did not finish / نصب سمت مقابل کامل نشد")
    profile.update(status="ready", message="Ready / آماده")
    _profile(root, peer_id, kind, profile)


def start_job(root: Path, peer_id: str, action: str, **kwargs) -> None:
    get(root, peer_id)
    if action == "provision" and kwargs.get("kind") not in KINDS:
        raise PeerError("Unknown tunnel / نوع تونل نامعتبر است")
    with _LOCK:
        if peer_id in _JOBS:
            raise PeerError("Server operation already running / عملیات سرور در حال اجراست")
        _JOBS.add(peer_id)
        _update(root, peer_id, status="working", message="Working / در حال انجام")

    def work():
        try:
            if action == "test":
                test_server(root, peer_id)
            elif action == "provision":
                provision(root, peer_id, **kwargs)
            else:
                raise PeerError("Unknown operation")
            _update(root, peer_id, status="connected", message="Done / انجام شد")
        except Exception as exc:
            message = str(exc) if isinstance(exc, PeerError) else "Operation failed; check connection / عملیات ناموفق؛ اتصال را بررسی کنید"
            _update(root, peer_id, status="error", message=message)
            if action == "provision":
                profile = get(root, peer_id).get("transports", {}).get(kwargs["kind"])
                if profile:
                    _profile(root, peer_id, kwargs["kind"], profile | {"status": "error", "message": message})
        finally:
            with _LOCK:
                _JOBS.discard(peer_id)
    threading.Thread(target=work, name="peer-setup-" + peer_id, daemon=True).start()


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, bytes]:
    from . import ops
    return ops._docker(method, path, payload)


def _owned(root: Path, name: str) -> dict | None:
    status, raw = api("GET", f"/containers/{name}/json")
    if status == 404:
        return None
    if status != 200:
        raise PeerError("Docker unavailable / داکر در دسترس نیست")
    info = json.loads(raw)
    if info.get("Config", {}).get("Labels", {}).get("outlander.peer.instance") != load(root)["instance"]:
        raise PeerError("Container is not managed by this console / کانتینر متعلق به این کنسول نیست")
    return info


def _host_path(root: Path, path: Path) -> str:
    controller = os.environ.get("CONSOLE_CONTAINER", "ol-console")
    status, raw = api("GET", f"/containers/{controller}/json")
    if status != 200:
        raise PeerError("Console mount not found / مسیر میزبان کنسول پیدا نشد")
    mounts = json.loads(raw).get("Mounts", [])
    for mount in mounts:
        if mount.get("Destination") == str(root):
            return str(Path(mount["Source"]) / path.relative_to(root))
    raise PeerError("Console repository mount is missing / پوشهٔ پروژه به کنسول متصل نیست")


def ensure_ssh(root: Path, peer: dict) -> int:
    with _LOCK:
        key = str(root) + peer["id"]
        process = _SSH.get(key)
        profile = peer["transports"]["ssh"]
        if process is not None and process.poll() is None:
            return int(profile["socks_port"])
        port = profile.get("socks_port")
        if not port:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            profile["socks_port"] = port
            _profile(root, peer["id"], "ssh", profile)
        process = _spawn(root, peer, ["-N", "-o", "ExitOnForwardFailure=yes", "-D", f"127.0.0.1:{port}"])
        # No command output is generated by an SSH dynamic forward; drain on failure only.
        _SSH[key] = process
        for _ in range(30):
            if process.poll() is not None:
                process.communicate()
                raise PeerError("SSH tunnel could not start / تونل SSH شروع نشد")
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=0.1):
                    return int(port)
            except OSError:
                time.sleep(0.1)
        process.kill()
        process.communicate()
        raise PeerError("SSH tunnel startup timed out / زمان شروع تونل SSH تمام شد")


def forward_config(rule: dict, peer: dict, socks_port: int = 0) -> dict:
    kind = rule["transport"]
    profile = peer["transports"][kind]
    if kind == "ssh":
        outbound = {"protocol": "socks", "settings": {"servers": [{"address": "127.0.0.1", "port": socks_port}]}}
    else:
        user = {"id": profile["uuid"], "encryption": "none"}
        if kind == "reality":
            user["flow"] = "xtls-rprx-vision"
        outbound = {"protocol": "vless", "settings": {"vnext": [{"address": peer["host"],
                    "port": profile["port"], "users": [user]}]}, "streamSettings": stream(profile, kind)}
    host, _, port = rule["target"].partition(":")
    return {"log": {"loglevel": "warning"}, "inbounds": [{"protocol": "dokodemo-door",
            "listen": "0.0.0.0", "port": int(rule["listen"]), "settings": {"address": host,
            "port": int(port), "network": "tcp,udp" if rule["proto"] == "both" else rule["proto"]},
            "streamSettings": {"sockopt": {"tcpMaxSeg": 1200}}}], "outbounds": [outbound]}


def forward_name(rule: dict) -> str:
    return "ol-peer-fwd-" + rule["peer_rule_id"]


def create_forward(root: Path, rule: dict) -> None:
    rule = forward.normalise_rule(rule)
    peer = get(root, rule["peer_id"])
    profile = peer.get("transports", {}).get(rule["transport"], {})
    if profile.get("status") != "ready":
        raise PeerError("Prepare this tunnel on the server first / ابتدا این تونل را روی سرور آماده کنید")
    name = forward_name(rule)
    if _owned(root, name):
        raise PeerError("Forward already exists / فوروارد از قبل موجود است")
    socks_port = ensure_ssh(root, peer) if rule["transport"] == "ssh" else 0
    folder = directory(root) / "forwards" / rule["peer_rule_id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write(folder / "config.json", json.dumps(forward_config(rule, peer, socks_port)))
    os.chown(folder, 0, 65532)
    os.chmod(folder, 0o750)
    os.chown(folder / "config.json", 0, 65532)
    os.chmod(folder / "config.json", 0o640)
    host_folder = _host_path(root, folder)
    container = {"Image": IMAGE, "Cmd": ["run", "-c", "/etc/xray/config.json"],
                 "Labels": {"outlander.peer.instance": load(root)["instance"]},
                 "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"},
                                "Binds": [host_folder + ":/etc/xray:ro"],
                                "LogConfig": {"Type": "json-file", "Config": {"max-size": "5m", "max-file": "2"}}}}
    status, _ = api("POST", f"/containers/create?name={name}", container)
    if status != 201:
        raise PeerError("Could not create isolated forward / ساخت فوروارد مستقل ناموفق بود")
    status, _ = api("POST", f"/containers/{name}/start")
    if status not in {204, 304}:
        api("DELETE", f"/containers/{name}?force=1")
        raise PeerError("Could not start forward / شروع فوروارد ناموفق بود")
    # Running is insufficient: a restarting process can briefly appear running.
    for _ in range(30):
        info = _owned(root, name)
        if not info or not info.get("State", {}).get("Running"):
            break
        if rule["proto"] == "udp":
            time.sleep(0.3)
            if (_owned(root, name) or {}).get("State", {}).get("Running"):
                return
            break
        try:
            with socket.create_connection(("127.0.0.1", int(rule["listen"])), timeout=0.1):
                return
        except OSError:
            time.sleep(0.1)
    api("DELETE", f"/containers/{name}?force=1")
    raise PeerError("Forward did not open its listener / پورت فوروارد باز نشد")


def delete_forward(root: Path, rule: dict) -> None:
    rule = forward.normalise_rule(rule)
    name = forward_name(rule)
    if _owned(root, name):
        status, _ = api("DELETE", f"/containers/{name}?force=1")
        if status not in {204, 404}:
            raise PeerError("Could not stop forward / توقف فوروارد ناموفق بود")
    config = directory(root) / "forwards" / rule["peer_rule_id"] / "config.json"
    config.unlink(missing_ok=True)


def delete_server(root: Path, peer_id: str) -> None:
    get(root, peer_id)
    with _LOCK:
        if peer_id in _JOBS or any(r.get("peer_id") == peer_id for r in forward.load(root)["rules"]):
            raise PeerError("Remove server forwards and wait for operations first / ابتدا فورواردهای این سرور را حذف کنید")
        peer = get(root, peer_id)
        if any(p.get("status") == "ready" and k != "ssh" for k, p in peer.get("transports", {}).items()):
            raise PeerError("Remove remote tunnel services first / ابتدا سرویس‌های تونل سمت مقابل را حذف کنید")
        process = _SSH.pop(str(root) + peer_id, None)
        if process and process.poll() is None:
            process.terminate()
        data = load(root)
        data["servers"] = [p for p in data["servers"] if p["id"] != peer_id]
        _save(root, data)
        for name in ("identity", "known_hosts", "known_hosts.old"):
            (directory(root) / peer_id / name).unlink(missing_ok=True)


def remove_transport(root: Path, peer_id: str, kind: str) -> None:
    if kind not in KINDS:
        raise PeerError("Unknown tunnel")
    if peer_id in _JOBS:
        raise PeerError("Server operation already running / عملیات سرور در حال اجراست")
    if any(r.get("peer_id") == peer_id and r.get("transport") == kind for r in forward.load(root)["rules"]):
        raise PeerError("Remove this tunnel's forwards first / ابتدا فورواردهای این تونل را حذف کنید")
    peer = get(root, peer_id)
    if kind != "ssh" and kind in peer.get("transports", {}):
        instance = load(root)["instance"]
        name = f"ol-peer-{instance}-{peer_id}-{kind}"
        run_ssh(root, peer, f'''set -eu
if docker inspect {name} >/dev/null 2>&1; then
 test "$(docker inspect -f '{{{{ index .Config.Labels "outlander.peer.instance" }}}}' {name})" = {instance}
 docker rm -f {name}
fi
''')
        # Keep remote config for recovery; no recursive deletion on a peer host.
    if kind == "ssh":
        process = _SSH.pop(str(root) + peer_id, None)
        if process and process.poll() is None:
            process.terminate()
    transports = peer.get("transports", {})
    transports.pop(kind, None)
    _update(root, peer_id, transports=transports)


def start_worker(root: Path) -> None:
    for peer in load(root)["servers"]:
        if peer.get("status") == "working":
            _update(root, peer["id"], status="error", message="Interrupted; retry / عملیات قطع شده؛ دوباره تلاش کنید")
    def work():
        while True:
            try:
                for rule in forward.load(root)["rules"]:
                    if rule.get("mode") == "peer" and rule.get("transport") == "ssh":
                        try:
                            ensure_ssh(root, get(root, rule["peer_id"]))
                        except PeerError:
                            pass
            except Exception:
                pass  # Never affect the existing auto-forward worker.
            time.sleep(15)
    threading.Thread(target=work, name="peer-ssh", daemon=True).start()
