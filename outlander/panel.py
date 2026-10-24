"""Route the panel's own Xray traffic through the gateway.

Two panel generations are supported:

* **3.x** (``3x-ui 3.x``): HTTP API, ``POST /panel/api/xray/update`` with a CSRF token.
* **2.x** (classic x-ui): the ``xrayTemplateConfig`` row inside the SQLite database.
"""

from __future__ import annotations

import http.cookiejar
import json
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TEMPLATE_KEY = "xrayTemplateConfig"


class PanelError(RuntimeError):
    """Raised when the panel refuses the template."""


def backup(db_path: Path) -> Path:
    target = db_path.with_suffix(".db.bak")
    shutil.copy2(db_path, target)
    return target


def has_legacy_template(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT 1 FROM settings WHERE key = ?", (TEMPLATE_KEY,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def merge_config(config: dict, outbound: dict, rule: dict) -> dict:
    """Idempotently add the gateway outbound and the catch-all routing rule."""
    outbounds = [item for item in config.get("outbounds", []) if item.get("tag") != outbound["tag"]]
    outbounds.append(outbound)
    config["outbounds"] = outbounds

    routing = config.setdefault("routing", {})
    rules = [item for item in routing.get("rules", []) if item.get("outboundTag") != rule["outboundTag"]]
    rules.append(rule)
    routing["rules"] = rules
    routing.setdefault("domainStrategy", "AsIs")
    return config


# -- panel 2.x -------------------------------------------------------------
def patch_database(db_path: Path, outbound: dict, rule: dict) -> dict:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (TEMPLATE_KEY,)).fetchone()
        if not row:
            raise KeyError(TEMPLATE_KEY)
        template = json.loads(row[0]) if row[0] and row[0].strip() else {}
        template = merge_config(template, outbound, rule)
        payload = json.dumps(template, indent=2, ensure_ascii=False)
        connection.execute("UPDATE settings SET value = ? WHERE key = ?", (payload, TEMPLATE_KEY))
        connection.commit()
    finally:
        connection.close()
    return {
        "mode": "database",
        "outbounds": [item.get("tag") for item in template["outbounds"]],
        "rules": len(template["routing"]["rules"]),
    }


# -- panel 3.x -------------------------------------------------------------
def _open(url: str, data: bytes | None = None, headers: dict | None = None,
          opener: urllib.request.OpenerDirector | None = None, method: str | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with (opener or urllib.request.build_opener()).open(request, timeout=25) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise PanelError(f"{url} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise PanelError(f"cannot reach the panel at {url}: {exc.reason}") from exc


def _json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise PanelError(f"panel returned non-JSON: {raw[:120]!r}") from exc


def patch_api(base_url: str, username: str, password: str, outbound: dict, rule: dict) -> dict:
    base = base_url.rstrip("/")
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    _open(f"{base}/", opener=opener)
    token = str(_json(_open(f"{base}/csrf-token", opener=opener)).get("obj") or "")

    form = urllib.parse.urlencode({"username": username, "password": password}).encode()
    login = _json(
        _open(
            f"{base}/login",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": token},
            opener=opener,
            method="POST",
        )
    )
    if not login.get("success"):
        raise PanelError(f"panel login failed: {login.get('msg') or 'check PANEL_USERNAME/PANEL_PASSWORD'}")

    token = str(_json(_open(f"{base}/csrf-token", opener=opener)).get("obj") or "")
    headers = {"Content-Type": "application/json", "X-CSRF-Token": token}

    template = _json(_open(f"{base}/panel/api/xray/", data=b"{}", headers=headers, opener=opener, method="POST"))
    payload = json.loads(str(template.get("obj") or "{}"))
    config = json.loads(payload.get("xraySetting") or "{}")
    config = merge_config(config, outbound, rule)

    body = json.dumps(
        {"xraySetting": json.dumps(config, ensure_ascii=False), "outboundTestUrl": payload.get("outboundTestUrl") or ""}
    ).encode()
    response = _json(_open(f"{base}/panel/api/xray/update", data=body, headers=headers, opener=opener, method="POST"))
    if not response.get("success"):
        raise PanelError(f"panel rejected the template: {response.get('msg')}")

    try:
        _open(f"{base}/panel/api/server/restartXrayService", data=b"{}", headers=headers, opener=opener, method="POST")
    except PanelError:
        pass

    return {
        "mode": "api",
        "outbounds": [item.get("tag") for item in config["outbounds"]],
        "rules": len(config["routing"]["rules"]),
    }
