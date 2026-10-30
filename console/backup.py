"""Create and safely restore portable Outlander settings backups."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from outlander import forward
from outlander.uri import parse

FORMAT = "outlander-settings-backup"
VERSION = 1
MANIFEST = "outlander-backup.json"
STATIC_FILES = ("config/inventory.json", "config/forwarding.json")
PEER_PREFIX = "data/peers/"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 4096
_ID = re.compile(r"^[a-f0-9]{12}$")


class BackupError(ValueError):
    """A backup is invalid or could not be safely applied."""


def _is_allowed(name: str) -> bool:
    return name in STATIC_FILES or name.startswith(PEER_PREFIX)


def _source_files(root: Path) -> list[Path]:
    if (root / "config").is_symlink():
        raise BackupError("Backup source contains a symbolic link")
    paths = [root / name for name in STATIC_FILES]
    peer_root = root / "data/peers"
    if peer_root.is_symlink():
        raise BackupError("Backup source contains a symbolic link")
    if peer_root.exists():
        paths.append(peer_root / "servers.json")
        for current, dirs, files in os.walk(peer_root, followlinks=False):
            base = Path(current)
            for name in dirs + files:
                candidate = base / name
                if candidate.is_symlink():
                    raise BackupError("Backup source contains a symbolic link")
            dirs[:] = sorted(dirs)
            for name in sorted(files):
                candidate = base / name
                if candidate.is_file():
                    paths.append(candidate)
    unique: dict[str, Path] = {}
    for path in paths:
        if not path.exists():
            if path.name == "servers.json":
                continue
            raise BackupError(f"Required setting is missing: {path.relative_to(root).as_posix()}")
        if path.is_symlink() or not path.is_file():
            raise BackupError("Backup source contains an unsupported file")
        unique[path.relative_to(root).as_posix()] = path
    return [unique[name] for name in sorted(unique)]


def create_archive(root: Path, destination: Path) -> None:
    """Write a private tar.gz containing editable settings and peer credentials."""
    from . import peers
    peers.load(root)
    files = _source_files(root)
    if len(files) > MAX_FILES:
        raise BackupError("Too many files in backup")
    entries = []
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        if total > MAX_TOTAL_BYTES:
            raise BackupError("Settings backup is too large")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        entries.append({"path": path.relative_to(root).as_posix(), "size": size, "sha256": digest})
    manifest = json.dumps({"format": FORMAT, "version": VERSION,
                           "created_at": datetime.now(timezone.utc).isoformat(),
                           "files": entries}, ensure_ascii=False, separators=(",", ":")).encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as archive:
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(manifest)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(manifest))
            for path in files:
                archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        os.chmod(destination, 0o600)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError("Backup contains an unsafe path")
    if not _is_allowed(name) or (name.startswith(PEER_PREFIX) and len(path.parts) < 3):
        raise BackupError("Backup contains an unsupported file")
    if name.startswith(PEER_PREFIX) and name != "data/peers/servers.json":
        if path.parts[2] == "forwards":
            if len(path.parts) > 3 and not _ID.fullmatch(path.parts[3]):
                raise BackupError("Backup contains an invalid forward identifier")
        elif not _ID.fullmatch(path.parts[2]):
            raise BackupError("Backup contains an invalid peer identifier")
    return path


def _validate_payload(stage: Path) -> None:
    from . import ops

    try:
        inventory = json.loads((stage / STATIC_FILES[0]).read_text(encoding="utf-8"))
        forwarding = json.loads((stage / STATIC_FILES[1]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup settings are missing or invalid") from exc
    if not isinstance(inventory, dict) or not isinstance(inventory.get("channels"), list):
        raise BackupError("Backup outbound settings are invalid")
    try:
        for channel in inventory["channels"]:
            if not isinstance(channel, dict) or not isinstance(channel.get("link"), str):
                raise ValueError("invalid channel")
            parse(channel["link"])
    except Exception as exc:
        raise BackupError("Backup contains an invalid outbound link") from exc
    if not isinstance(forwarding, dict) or not isinstance(forwarding.get("rules"), list):
        raise BackupError("Backup forwarding settings are invalid")
    try:
        hop = forwarding.get("hop", {})
        if not isinstance(hop, dict) or hop.get("proto", "udp") not in forward.PROTOS:
            raise ValueError("invalid port hopping settings")
        port_range = str(hop.get("range", "20000-40000"))
        if not forward.PORT_RE.fullmatch(port_range):
            raise ValueError("invalid port hopping range")
        if "-" in port_range:
            low, _, high = port_range.partition("-")
            if not 0 < int(low) <= int(high) < 65536:
                raise ValueError("invalid port hopping range")
        else:
            if not 0 < int(port_range) < 65536:
                raise ValueError("invalid port hopping port")
        forward.normalise_target(str(hop.get("target", "172.28.60.12:8443")))
        for rule in forwarding["rules"]:
            if not isinstance(rule, dict):
                raise ValueError("invalid rule")
            ops._assert_not_self_forward(forward.normalise_rule(rule))
    except (TypeError, ValueError) as exc:
        raise BackupError("Backup contains an invalid forwarding rule") from exc

    peers_file = stage / "data/peers/servers.json"
    if peers_file.exists():
        try:
            data = json.loads(peers_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError("Backup peer-server data is invalid") from exc
        if not isinstance(data, dict) or not _ID.fullmatch(str(data.get("instance", ""))) or not isinstance(data.get("servers"), list):
            raise BackupError("Backup peer-server data is invalid")
        seen: set[str] = set()
        for peer in data["servers"]:
            if not isinstance(peer, dict) or not _ID.fullmatch(str(peer.get("id", ""))) or peer["id"] in seen:
                raise BackupError("Backup contains an invalid peer server")
            seen.add(peer["id"])
            if not isinstance(peer.get("host"), str) or not isinstance(peer.get("username"), str):
                raise BackupError("Backup contains an invalid peer server")


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def restore_archive(root: Path, source: Path) -> None:
    """Validate a complete archive before atomically replacing settings and peers."""
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("Backup file is too large")
    root = root.resolve()
    stage_root = Path(tempfile.mkdtemp(prefix=".outlander-restore-", dir=root))
    stage = stage_root / "payload"
    stage.mkdir(mode=0o700)
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) < 2 or len(members) > MAX_FILES + 1:
                raise BackupError("Backup file list is invalid")
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                if not member.isfile() or member.issym() or member.islnk():
                    raise BackupError("Backup contains unsupported entries")
                if member.name == MANIFEST:
                    if member.name in by_name or member.size > 1024 * 1024:
                        raise BackupError("Backup manifest is invalid")
                else:
                    _member_path(member.name)
                    if member.name in by_name:
                        raise BackupError("Backup contains duplicate files")
                by_name[member.name] = member
            manifest_member = by_name.get(MANIFEST)
            manifest_file = archive.extractfile(manifest_member) if manifest_member else None
            manifest = json.loads(manifest_file.read(1024 * 1024 + 1).decode("utf-8")) if manifest_file else None
            if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                    or manifest.get("version") != VERSION or not isinstance(manifest.get("files"), list)):
                raise BackupError("This is not a supported Outlander settings backup")
            declared: dict[str, dict] = {}
            for entry in manifest["files"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise BackupError("Backup manifest is invalid")
                _member_path(entry["path"])
                if entry["path"] in declared:
                    raise BackupError("Backup manifest contains duplicate files")
                declared[entry["path"]] = entry
            actual_names = set(by_name) - {MANIFEST}
            if set(declared) != actual_names or len(declared) != len(manifest["files"]):
                raise BackupError("Backup manifest does not match its files")
            total = 0
            for name, member in by_name.items():
                if name == MANIFEST:
                    continue
                entry = declared[name]
                if not isinstance(entry.get("size"), int) or entry["size"] != member.size:
                    raise BackupError("Backup file size does not match its manifest")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise BackupError("Expanded backup is too large")
                target = stage / Path(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError("Backup file could not be read")
                digest = hashlib.sha256()
                with stream, target.open("xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise BackupError("Backup file ended unexpectedly")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                os.chmod(target, 0o600)
                if not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256", ""))) or digest.hexdigest() != entry["sha256"]:
                    raise BackupError("Backup integrity check failed")
        if not {stage / name for name in STATIC_FILES}.issubset(set(stage.rglob("*"))):
            raise BackupError("Backup is missing required settings")
        _validate_payload(stage)

        targets = [root / name for name in STATIC_FILES] + [root / "data/peers"]
        for target in targets:
            if target.is_symlink() or target.parent.is_symlink():
                raise BackupError("A destination path is a symbolic link")
        rollback = stage_root / "previous"
        rollback.mkdir(mode=0o700)
        replaced: list[tuple[Path, Path | None]] = []
        try:
            staged_targets = [stage / name for name in STATIC_FILES] + [stage / "data/peers"]
            for target, staged in zip(targets, staged_targets):
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                old = None
                if target.exists():
                    old = rollback / str(len(replaced))
                    os.replace(target, old)
                replaced.append((target, old))
                if staged.exists():
                    os.replace(staged, target)
                elif target.name == "peers":
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    (target / "servers.json").write_text(
                        json.dumps({"instance": os.urandom(6).hex(), "servers": []}, indent=2) + "\n",
                        encoding="utf-8")
                    os.chmod(target / "servers.json", 0o600)
        except Exception:
            for target, old in reversed(replaced):
                _remove(target)
                if old is not None and old.exists():
                    os.replace(old, target)
            raise
    except BackupError:
        raise
    except (OSError, EOFError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup file is damaged or could not be read") from exc
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
