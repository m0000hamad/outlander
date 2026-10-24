"""Route the panel's own Xray traffic through the gateway, and drive the panel API.

Two panel generations are supported for the template patch:

* **3.x** (``3x-ui 3.x``): HTTP API, ``POST /panel/api/xray/update`` with a CSRF token.
* **2.x** (classic x-ui): the ``xrayTemplateConfig`` row inside the SQLite database.

``PanelClient`` is the small 3.x client the console uses to create proxy inbounds.
"""

from __future__ import annotations

import http.cookiejar
import json
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TEMPLATE_KEY = "xrayTemplateConfig"


class PanelError(RuntimeError):
    """Raised when the panel refuses a request."""


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


# -- HTTP plumbing ---------------------------------------------------------
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


# -- panel 3.x template ----------------------------------------------------
def patch_api(base_url: str, username: str, password: str, outbound: dict, rule: dict) -> dict:
    client = PanelClient(base_url, username, password).login()
    template = client.post_json("/panel/api/xray/", {})
    payload = json.loads(str(template.get("obj") or "{}"))
    raw_setting = payload.get("xraySetting")
    if isinstance(raw_setting, dict):
        config = raw_setting
    elif isinstance(raw_setting, str) and raw_setting.strip():
        config = json.loads(raw_setting)
    else:
        config = {}
    config = merge_config(config, outbound, rule)
    test_url = payload.get("outboundTestUrl") or ""

    # 3.x reads the template as a form field (JSON in a form body), not as a JSON body.
    form = urllib.parse.urlencode(
        {"xraySetting": json.dumps(config, ensure_ascii=False), "outboundTestUrl": test_url}
    ).encode()
    response = client.post_form("/panel/api/xray/update", form)
    if not response.get("success"):
        raise PanelError(f"panel rejected the template: {response.get('msg')}")

    try:
        client.post_json("/panel/api/server/restartXrayService", {})
    except PanelError:
        pass

    return {
        "mode": "api",
        "outbounds": [item.get("tag") for item in config["outbounds"]],
        "rules": len(config["routing"]["rules"]),
    }


class PanelClient:
    """Minimal 3.x client: login, CSRF, inbounds and key helpers."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self._token = ""

    # -- low level --------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self._token} if self._token else {}

    def get(self, path: str) -> dict:
        return _json(_open(f"{self.base}{path}", headers=self._headers(), opener=self._opener))

    def post_form(self, path: str, body: bytes) -> dict:
        headers = {"Content-Type": "application/x-www-form-urlencoded", **self._headers()}
        return _json(
            _open(f"{self.base}{path}", data=body, headers=headers, opener=self._opener, method="POST")
        )

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json", **self._headers()}
        body = json.dumps(payload or {}).encode()
        return _json(
            _open(f"{self.base}{path}", data=body, headers=headers, opener=self._opener, method="POST")
        )

    def refresh_token(self) -> None:
        self._token = str(self.get("/csrf-token").get("obj") or "")

    # -- session ----------------------------------------------------------
    def login(self) -> "PanelClient":
        _open(f"{self.base}/", opener=self._opener)
        self.refresh_token()
        form = urllib.parse.urlencode({"username": self.username, "password": self.password}).encode()
        response = _json(
            _open(
                f"{self.base}/login",
                data=form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRF-Token": self._token,
                },
                opener=self._opener,
                method="POST",
            )
        )
        if not response.get("success"):
            raise PanelError(
                f"panel login failed: {response.get('msg') or 'check PANEL_USERNAME/PANEL_PASSWORD'}"
            )
        self.refresh_token()
        return self

    # -- inbounds ---------------------------------------------------------
    def list_inbounds(self) -> list[dict]:
        payload = self.get("/panel/api/inbounds/list")
        return payload.get("obj") or []

    def add_inbound(self, payload: dict) -> dict:
        response = self.post_json("/panel/api/inbounds/add", payload)
        if not response.get("success"):
            raise PanelError(response.get("msg") or "panel rejected the inbound")
        return response.get("obj") or {}

    def delete_inbound(self, inbound_id: int) -> dict:
        response = self.post_json(f"/panel/api/inbounds/del/{inbound_id}", {})
        if not response.get("success"):
            raise PanelError(response.get("msg") or "panel could not delete the inbound")
        return response

    # -- helpers ----------------------------------------------------------
    def new_uuid(self) -> str:
        try:
            return str(self.get("/panel/api/server/getNewUUID")["obj"]["uuid"])
        except (KeyError, TypeError):
            return str(uuid.uuid4())

    def new_reality_keys(self) -> tuple[str, str]:
        payload = self.get("/panel/api/server/getNewX25519Cert")
        obj = payload.get("obj") or {}
        private = obj.get("privateKey")
        public = obj.get("publicKey")
        if not private or not public:
            raise PanelError("panel did not return a REALITY keypair")
        return private, public
