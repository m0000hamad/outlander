"""Persistent client-side IP byte accounting for every forwarding transport.

Observe INPUT/OUTPUT for local relays and FORWARD for DNAT, using the original
conntrack tuple so translated ports and port ranges retain their identity.
The separate counter table never changes a packet verdict or forwarding rule.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from outlander import forward

TABLE = "outlander_usage"
INTERVAL = 5
_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_STARTED = False


def rule_id(raw: dict) -> str:
    rule = forward.normalise_rule(raw)
    # Keep totals when auto switches transport, or the user reorders the rows.
    key = [rule[k] for k in ("proto", "listen", "listen_ip", "target")]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def ruleset(rules: list[dict], epoch: str, exists: bool) -> str:
    lines = [f"delete table inet {TABLE}"] if exists else []
    lines += [f"table inet {TABLE} {{", f'  comment "{epoch}"']
    unique = {rule_id(raw): forward.normalise_rule(raw) for raw in rules}
    for key in unique:
        lines += [f"  counter up_{key} {{ }}", f"  counter down_{key} {{ }}"]
    for hook in ("input", "output", "forward"):
        lines += [f"  chain {hook} {{", f"    type filter hook {hook} priority 10; policy accept;"]
        directions = ("original", "reply") if hook == "forward" else (("original",) if hook == "input" else ("reply",))
        for key, rule in unique.items():
            for direction in directions:
                parts = [f"ct direction {direction}"]
                if hook == "forward":
                    parts.append("ct status dnat")
                if rule["listen_ip"]:
                    parts.append(f"ct original ip daddr {rule['listen_ip']}")
                proto = rule["proto"]
                parts.append("meta l4proto { tcp, udp }" if proto == "both" else f"meta l4proto {proto}")
                if rule["listen"] != "*":
                    parts.append(f"ct original proto-dst {rule['listen']}")
                name = "up" if direction == "original" else "down"
                # First matching rule owns overlapping ranges, like DNAT itself.
                lines.append("    " + " ".join(parts) + f" counter name {name}_{key} return")
        lines.append("  }")
    return "\n".join(lines + ["}", ""])


def _nft(args: list[str], content: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(["nft", *args], input=content, capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError((result.stderr or "nft counter read failed").strip()[:500])
    return result


def _read() -> tuple[str, dict[str, dict[str, int]]] | None:
    result = subprocess.run(["nft", "-j", "list", "table", "inet", TABLE],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        # Do not mistake a permissions/command failure for a reset.
        if "No such file or directory" in result.stderr:
            return None
        raise RuntimeError((result.stderr or "nft counter read failed").strip()[:500])
    epoch = ""
    counters: dict[str, dict[str, int]] = {}
    for item in json.loads(result.stdout)["nftables"]:
        if "table" in item:
            epoch = item["table"].get("comment", "")
        counter = item.get("counter", {})
        name = counter.get("name", "")
        direction, _, key = name.partition("_")
        if direction in {"up", "down"}:
            counters.setdefault(key, {})[direction] = int(counter.get("bytes", 0))
    return epoch, counters


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, separators=(",", ":"))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _accumulate(state: dict, epoch: str, counters: dict, now: float) -> None:
    same_epoch = state.get("epoch") == epoch
    for key, values in counters.items():
        entry = state.setdefault("entries", {}).setdefault(key, {"since": now, "up": 0, "down": 0, "raw": {}})
        for direction in ("up", "down"):
            current = values.get(direction, 0)
            previous = entry.get("raw", {}).get(direction, 0) if same_epoch else 0
            entry[direction] += current - previous if current >= previous else current
        entry["raw"] = values
    state["epoch"] = epoch
    state["updated_at"] = now


def sample(root: Path) -> dict:
    """Read totals and reconcile counters; errors retain the last known totals."""
    with _LOCK:
        path = root / "data/console/forward-traffic.json"
        state = {"entries": {}}
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
            rules = forward.load(root).get("rules", [])
            keys = [rule_id(rule) for rule in rules]
            signature = hashlib.sha256(json.dumps(keys).encode()).hexdigest()[:16]
            snapshot = _read()
            now = time.time()
            if snapshot is not None:
                _accumulate(state, *snapshot, now)
                # Save the old counters before any atomic table replacement.
                _save(path, state)
            if snapshot is None or not snapshot[0].startswith(f"v1-{signature}-"):
                epoch = f"v1-{signature}-{secrets.token_hex(8)}"
                _nft(["-f", "-"], ruleset(rules, epoch, snapshot is not None))
                _accumulate(state, epoch, {key: {"up": 0, "down": 0} for key in keys}, now)
                _save(path, state)
            return _response(state, keys)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
            logging.getLogger(__name__).debug("Traffic accounting unavailable: %s", exc)
            # A failed read is unavailable, never a fabricated zero or reset.
            return {"available": False, "updated_at": state.get("updated_at"), "rules": {}}


def _response(state: dict, keys: list[str]) -> dict:
    return {"available": True, "updated_at": state.get("updated_at"),
            "rules": {key: {field: state["entries"][key][field] for field in ("up", "down", "since")}
                      for key in keys}}


def start_worker(root: Path) -> None:
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True

    def work() -> None:
        while True:
            sample(root)
            time.sleep(INTERVAL)

    threading.Thread(target=work, name="forward-traffic", daemon=True).start()
