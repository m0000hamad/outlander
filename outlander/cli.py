"""Command line entry point: python3 -m outlander <command>."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import forward, panel, render
from .uri import LinkError, parse

VERSION = "1.0.0"

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def repo_root() -> Path:
    env = os.environ.get("OUTLANDER_ROOT")
    if env:
        return Path(env).resolve()
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    return Path(__file__).resolve().parents[1]


def _load(root: Path) -> dict:
    return render.load(root)


def _save(root: Path, inventory: dict) -> None:
    render.save(root, inventory)


def cmd_init(args: argparse.Namespace) -> int:
    root = repo_root()
    inventory = render.load(root)
    render.save(root, inventory)
    print(f"inventory ready: {root / render.INVENTORY}")
    print(f"secret: {inventory['secret']}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    root = repo_root()
    inventory = _load(root)
    try:
        channel = render.add_channel(
            inventory,
            args.link,
            tag=args.tag,
            group=args.group,
            resolve=args.resolve or "",
            via=args.via or "",
        )
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _save(root, inventory)
    link = parse(channel["link"])
    print(f"added {channel['tag']} -> {link.describe()} [{render.outbound_tag(channel)}]")
    if not args.no_render:
        render.render(root)
        print("configs rendered")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = repo_root()
    inventory = _load(root)
    channels = inventory.get("channels", [])
    if not channels:
        print("no outbound channels yet - add one with: outlander add '<link>'")
        return 0
    width = max(len(channel["tag"]) for channel in channels) + 2
    print(f"{'TAG'.ljust(width)}GROUP      PROTOCOL    ENDPOINT")
    for channel in channels:
        link = parse(channel["link"])
        state = "" if channel.get("enabled", True) else " (disabled)"
        via = f" via {channel['via']}" if channel.get("via") else ""
        print(
            f"{channel['tag'].ljust(width)}"
            f"{(channel.get('group') or 'cdn').ljust(11)}"
            f"{link.scheme.ljust(12)}"
            f"{link.address}:{link.port}{state}{via}"
        )
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    root = repo_root()
    inventory = _load(root)
    channel = render.find(inventory, args.tag)
    if not channel:
        print(f"error: no channel named '{args.tag}'", file=sys.stderr)
        return 2
    inventory["channels"].remove(channel)
    for other in inventory["channels"]:
        if other.get("via") == args.tag:
            other["via"] = ""
    _save(root, inventory)
    render.render(root)
    print(f"removed {args.tag}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    root = repo_root()
    paths = render.render(root)
    for label, path in paths.items():
        print(f"{label.ljust(10)} {path}")
    return 0 if validate(root) else 1


def validate(root: Path) -> bool:
    ok = True
    for path in (root / render.GATEWAY_CONFIG, root / render.SINGBOX_CONFIG):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"invalid {path.name}: {exc}", file=sys.stderr)
            ok = False
    return ok


def cmd_test(args: argparse.Namespace) -> int:
    root = repo_root()
    if not validate(root):
        return 1
    if not shutil.which("docker"):
        print("docker not found; JSON syntax is valid, core test skipped")
        return 0
    gateway = root / render.GATEWAY_CONFIG
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{gateway}:/etc/xray/config.json:ro",
        "ghcr.io/xtls/xray-core:latest",
        "-test",
        "-c",
        "/etc/xray/config.json",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip())
    return result.returncode


def cmd_panel_patch(args: argparse.Namespace) -> int:
    root = repo_root()
    inventory = _load(root)
    env = render.read_env(root)
    db = Path(args.db) if args.db else root / "data/x-ui/x-ui.db"
    outbound, rule = render.panel_fragments(inventory, env)

    legacy = bool(args.db) or panel.has_legacy_template(db)
    if not legacy:
        base = args.base or f"http://127.0.0.1:{env.get('PANEL_PORT', '2053')}"
        try:
            summary = panel.patch_api(
                base,
                env.get("PANEL_USERNAME", "admin"),
                env.get("PANEL_PASSWORD", ""),
                outbound,
                rule,
            )
        except panel.PanelError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"panel template patched through the 3.x API ({base})")
        print(f"outbounds: {', '.join(str(tag) for tag in summary['outbounds'])} | routing rules: {summary['rules']}")
        return 0

    if not db.exists():
        print(f"error: {db} not found - start the panel once so it creates its database", file=sys.stderr)
        return 2

    stopped = False
    if not args.no_stop and shutil.which("docker"):
        stop = subprocess.run(["docker", "stop", "ol-panel"], capture_output=True, text=True)
        stopped = stop.returncode == 0
        if stopped:
            print("panel paused while patching")

    backup = None
    try:
        backup = panel.backup(db)
        summary = panel.patch_database(db, outbound, rule)
    except PermissionError:
        print(f"error: no permission to write {db} - re-run with sudo", file=sys.stderr)
        if stopped and shutil.which("docker"):
            subprocess.run(["docker", "start", "ol-panel"], check=False)
        return 1
    except KeyError:
        print("error: xrayTemplateConfig row not found in the panel database", file=sys.stderr)
        if stopped and shutil.which("docker"):
            subprocess.run(["docker", "start", "ol-panel"], check=False)
        return 2
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: could not patch the panel database: {exc}", file=sys.stderr)
        if stopped and shutil.which("docker"):
            subprocess.run(["docker", "start", "ol-panel"], check=False)
        return 1

    print(f"panel template patched (backup: {backup.name})")
    print(f"outbounds: {', '.join(summary['outbounds'])} | routing rules: {summary['rules']}")
    if stopped and shutil.which("docker"):
        subprocess.run(["docker", "start", "ol-panel"], check=False)
        print("panel restarted")
    print("restart xray from the panel UI (or `docker restart ol-panel`) to apply")
    return 0


def cmd_forward_render(args: argparse.Namespace) -> int:
    root = repo_root()
    path = forward.render(root)
    print(f"wrote {path}")
    print("apply with: sudo bash scripts/forward-apply.sh")
    return 0


def cmd_forward_add(args: argparse.Namespace) -> int:
    root = repo_root()
    data = forward.load(root)
    try:
        rule = forward.normalise_rule(
            {
                "proto": args.proto,
                "listen": args.listen,
                "listen_ip": args.listen_ip or "",
                "target": args.target,
            }
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    conflicts = forward.conflicting_ports(rule)
    if conflicts and not args.force:
        described = []
        for port in conflicts:
            owner = forward.listener_owner(port)
            described.append(f"{port} [{owner}]" if owner else str(port))
        print(
            f"error: {', '.join(described)} already in use on this host; DNAT would hijack that service. "
            "Pick another port or use --listen-ip, or --force if you really mean to take the port over.",
            file=sys.stderr,
        )
        return 2
    duplicate = forward.find_duplicate(data["rules"], rule)
    if duplicate:
        print(
            f"error: a rule for {duplicate.get('listen')} already exists ({duplicate.get('proto')} -> "
            f"{duplicate.get('target')})",
            file=sys.stderr,
        )
        return 2
    data["rules"].append(rule)
    forward.save(root, data)
    forward.render(root)
    print(f"forward {rule['proto']}/{rule['listen']} -> {rule['target']} added")
    return 0


def cmd_forward_list(args: argparse.Namespace) -> int:
    root = repo_root()
    data = forward.load(root)
    hop = data.get("hop", {})
    if hop.get("enabled"):
        print(f"hop        {hop.get('proto', 'udp')}/{hop['range']} -> {hop['target']}")
    for rule in data.get("rules", []):
        print(f"forward    {rule.get('proto')}/{rule.get('listen')} -> {rule.get('target')}")
    if not data.get("rules") and not hop.get("enabled"):
        print("no forwarding rules")
    return 0


def _check(label: str, state: str, detail: str = "") -> str:
    colour = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}.get(state, "")
    reset = "\033[0m" if colour else ""
    print(f"[{colour}{state}{reset}] {label}" + (f" - {detail}" if detail else ""))
    return state


def cmd_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    results: list[str] = []

    version = sys.version_info
    results.append(
        _check("python", PASS if version >= (3, 9) else FAIL, f"{version.major}.{version.minor}.{version.micro}")
    )
    results.append(_check("docker", PASS if shutil.which("docker") else FAIL))
    if shutil.which("docker"):
        compose = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        results.append(_check("docker compose", PASS if compose.returncode == 0 else FAIL))
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
        results.append(_check("docker daemon", PASS if info.returncode == 0 else FAIL, info.stdout.strip()))

    env_path = root / ".env"
    results.append(_check(".env", PASS if env_path.exists() else WARN, "copy .env.example" if not env_path.exists() else ""))
    env = render.read_env(root)
    for key in ("PANEL_PORT", "GATEWAY_SOCKS_PORT"):
        results.append(_check(f"env {key}", PASS if env.get(key) else WARN, env.get(key, "unset")))

    inventory = _load(root)
    count = len(inventory.get("channels", []))
    results.append(_check("outbound channels", PASS if count else WARN, f"{count} channel(s)"))
    for channel in inventory.get("channels", []):
        try:
            parse(channel["link"])
        except LinkError as exc:
            results.append(_check(f"channel {channel['tag']}", FAIL, str(exc)))

    for path in (root / render.GATEWAY_CONFIG, root / render.SINGBOX_CONFIG):
        if not path.exists():
            results.append(_check(path.name, FAIL, "run `outlander render`"))
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            results.append(_check(path.name, PASS))
        except json.JSONDecodeError as exc:
            results.append(_check(path.name, FAIL, str(exc)))

    if env.get("HY2_ENABLED", "0") in {"1", "true", "yes"}:
        cert = root / "certs/fullchain.pem"
        key = root / "certs/privkey.pem"
        results.append(_check("hysteria2 certificate", PASS if cert.exists() and key.exists() else FAIL, "run scripts/cert.sh"))

    if Path("/proc/sys/net/ipv4/tcp_congestion_control").exists():
        cc = Path("/proc/sys/net/ipv4/tcp_congestion_control").read_text().strip()
        results.append(_check("tcp congestion control", PASS if cc == "bbr" else WARN, cc))

    results.append(_check("nftables", PASS if shutil.which("nft") else WARN, "needed for port forwarding"))
    results.append(_check("sqlite3", PASS if shutil.which("sqlite3") else WARN, "needed by panel scripts"))
    if shutil.which("docker"):
        running = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        for name in ("ol-gateway", "ol-panel"):
            results.append(_check(name, PASS if name in running else WARN, "not running"))

    return 1 if FAIL in results else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outlander", description="Outlander proxy gateway toolkit")
    parser.add_argument("--version", action="version", version=f"outlander {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create config/inventory.json").set_defaults(func=cmd_init)

    add = sub.add_parser("add", help="add an upstream outbound from a share link")
    add.add_argument("link")
    add.add_argument("--tag")
    add.add_argument("--group", default="cdn")
    add.add_argument("--resolve", help="pin the upstream hostname to this IP")
    add.add_argument("--via", help="dial through another channel tag first")
    add.add_argument("--no-render", action="store_true")
    add.set_defaults(func=cmd_add)

    listing = sub.add_parser("list", help="list upstream channels")
    listing.set_defaults(func=cmd_list)

    rm = sub.add_parser("rm", help="remove an upstream channel")
    rm.add_argument("tag")
    rm.set_defaults(func=cmd_rm)

    sub.add_parser("render", help="render xray and sing-box configs").set_defaults(func=cmd_render)
    sub.add_parser("test", help="validate rendered configs with xray -test").set_defaults(func=cmd_test)
    sub.add_parser("doctor", help="check the host and deployment").set_defaults(func=cmd_doctor)

    panel = sub.add_parser("panel", help="panel integration")
    panel_sub = panel.add_subparsers(dest="panel_command", required=True)
    patch = panel_sub.add_parser("patch", help="route panel traffic through the gateway")
    patch.add_argument("--db", help="force the classic (2.x) database patch")
    patch.add_argument("--base", help="panel base URL for the 3.x API (default http://127.0.0.1:PANEL_PORT)")
    patch.add_argument("--no-stop", action="store_true")
    patch.set_defaults(func=cmd_panel_patch)

    fwd = sub.add_parser("forward", help="port/IP forwarding")
    fwd_sub = fwd.add_subparsers(dest="forward_command", required=True)
    fwd_sub.add_parser("render", help="write nftables rules").set_defaults(func=cmd_forward_render)
    fwd_sub.add_parser("list", help="show forwarding rules").set_defaults(func=cmd_forward_list)
    fwd_add = fwd_sub.add_parser("add", help="add an L4 forward")
    fwd_add.add_argument("--proto", default="tcp", choices=["tcp", "udp", "both"])
    fwd_add.add_argument("--listen", required=True, help="port, range like 50000-50100, or * for every port")
    fwd_add.add_argument("--listen-ip", help="only forward traffic arriving on this IP")
    fwd_add.add_argument("--target", required=True, help="host or host:port")
    fwd_add.add_argument("--force", action="store_true", help="allow a port that already has a listener (dangerous)")
    fwd_add.set_defaults(func=cmd_forward_add)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
