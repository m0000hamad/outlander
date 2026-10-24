"""Outlander web console: an authenticated UI over the outlander operations."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ops, ui

USER = os.environ.get("CONSOLE_USER", "admin")
PORT = int(os.environ.get("CONSOLE_PORT", "8099"))
BIND = os.environ.get("CONSOLE_BIND", "0.0.0.0")
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL", str(12 * 3600)))
MAX_BODY = 2 * 1024 * 1024
PASSWORD_FILE = Path(os.environ.get("CONSOLE_PASSWORD_FILE", "/data/console-password.txt"))

_SECRET = secrets.token_bytes(32)
_SESSIONS: dict[str, float] = {}
_FAILS: dict[str, list[float]] = {}
_LOCK = threading.Lock()
_PASSWORD_CACHE: str = ""


def password() -> str:
    global _PASSWORD_CACHE
    if _PASSWORD_CACHE:
        return _PASSWORD_CACHE
    value = os.environ.get("CONSOLE_PASSWORD", "").strip()
    if not value:
        try:
            value = PASSWORD_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
    if not value:
        value = secrets.token_urlsafe(12)
        try:
            PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
            PASSWORD_FILE.write_text(value + "\n", encoding="utf-8")
        except OSError:
            pass
        print(f"[console] generated console password: {value}", flush=True)
    _PASSWORD_CACHE = value
    return value


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _SESSIONS[token] = time.time() + SESSION_TTL
    return token


def session_valid(token: str) -> bool:
    if not token:
        return False
    with _LOCK:
        expiry = _SESSIONS.get(token)
        if not expiry:
            return False
        if expiry < time.time():
            _SESSIONS.pop(token, None)
            return False
    return True


def csrf_for(token: str) -> str:
    return hmac.new(_SECRET, token.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def login_allowed(ip: str) -> bool:
    now = time.time()
    with _LOCK:
        attempts = [stamp for stamp in _FAILS.get(ip, []) if now - stamp < 600]
        _FAILS[ip] = attempts
        return len(attempts) < 8


def note_failure(ip: str) -> None:
    with _LOCK:
        _FAILS.setdefault(ip, []).append(time.time())


def _safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "OutlanderConsole/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- plumbing ---------------------------------------------------------
    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8",
              headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str, message: str = "", level: str = "ok") -> None:
        if message:
            message = message if len(message) <= 900 else message[:900] + "..."
            separator = "&" if "?" in location else "?"
            location = f"{location}{separator}flash={urllib.parse.quote(message)}&level={level}"
        self._send(303, b"", headers={"Location": location})

    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _session(self) -> str:
        token = self._cookie("ol_console")
        return token if session_valid(token) else ""

    def _form(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def _context(self, token: str, params: dict[str, list[str]]) -> dict:
        inventory = ops.inventory()
        channels = []
        for channel in inventory.get("channels", []):
            try:
                link = ops.parse(channel["link"])
                detail_parts = []
                if link.network != "tcp":
                    detail_parts.append(link.network)
                if link.security in {"tls", "reality"}:
                    detail_parts.append(link.security)
                if link.host:
                    detail_parts.append(f"host={link.host}")
                if link.path not in {"", "/"}:
                    detail_parts.append(f"path={link.path}")
                if channel.get("via"):
                    detail_parts.append(f"via={channel['via']}")
                channels.append(
                    {
                        "tag": channel["tag"],
                        "group": channel.get("group") or "cdn",
                        "enabled": bool(channel.get("enabled", True)),
                        "scheme": link.scheme,
                        "endpoint": f"{link.address}:{link.port}",
                        "detail": " · ".join(detail_parts) or "—",
                    }
                )
            except Exception:  # noqa: BLE001 - never break the page on one bad row
                channels.append(
                    {
                        "tag": channel.get("tag", "?"),
                        "group": channel.get("group") or "cdn",
                        "enabled": bool(channel.get("enabled", True)),
                        "scheme": "?",
                        "endpoint": "unparsed link",
                        "detail": "—",
                    }
                )
        forwarding = ops.forwarding()
        env = ops.env()
        return {
            "csrf": csrf_for(token),
            "channels": channels,
            "rules": forwarding.get("rules", []),
            "hop": forwarding.get("hop", {}),
            "states": {key: ops.container_state(key) for key in ("gateway", "panel", "singbox")},
            "state": ops.read_state(),
            "flash": ui.flash_html(
                params.get("flash", [""])[0], params.get("level", ["ok"])[0]
            ),
            "socks_port": env.get("GATEWAY_SOCKS_PORT", "10808"),
        }

    # -- verbs ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = urllib.parse.parse_qs(parsed.query)
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if path == "/login":
            if self._session():
                return self._redirect("/")
            return self._send(200, ui.login())
        token = self._session()
        if not token:
            return self._redirect("/login")
        context = self._context(token, params)
        if path == "/":
            return self._send(200, ui.dashboard(context))
        if path == "/outbounds":
            return self._send(200, ui.outbounds(context))
        if path == "/forwarding":
            return self._send(200, ui.forwarding(context))
        return self._send(404, ui.layout("404", "<h1>404</h1><p>صفحه پیدا نشد.</p>", "/"))

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        try:
            form = self._form()
        except ValueError as exc:
            self.close_connection = True
            return self._send(413, str(exc), "text/plain; charset=utf-8")

        if path == "/login":
            return self._login(form)

        token = self._session()
        if not token:
            return self._redirect("/login")
        if not _safe_equal(form.get("_csrf", ""), csrf_for(token)):
            return self._send(400, "csrf token mismatch", "text/plain; charset=utf-8")

        if path == "/logout":
            with _LOCK:
                _SESSIONS.pop(token, None)
            return self._send(
                303,
                b"",
                headers={"Location": "/login", "Set-Cookie": "ol_console=; Path=/; Max-Age=0"},
            )

        routes = {
            "/outbounds/add": self._outbounds_add,
            "/outbounds/delete": self._outbounds_delete,
            "/outbounds/toggle": self._outbounds_toggle,
            "/outbounds/apply": self._outbounds_apply,
            "/forwarding/add": self._forwarding_add,
            "/forwarding/delete": self._forwarding_delete,
            "/forwarding/hop": self._forwarding_hop,
            "/forwarding/apply": self._forwarding_apply,
            "/panel/route": self._panel_route,
            "/service/action": self._service_action,
        }
        handler = routes.get(path)
        if not handler:
            return self._send(404, "not found", "text/plain; charset=utf-8")
        try:
            target, message, level = handler(form)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            target, message, level = "/", f"{type(exc).__name__}: {exc}", "err"
        self._redirect(target, message, level)

    # -- auth -------------------------------------------------------------
    def _login(self, form: dict[str, str]) -> None:
        ip = self.client_address[0]
        if not login_allowed(ip):
            return self._send(429, ui.login("تلاش‌های ناموفق زیاد بود؛ چند دقیقه صبر کنید."))
        if form.get("user", "") == USER and _safe_equal(form.get("password", ""), password()):
            token = new_session()
            cookie = f"ol_console={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}"
            return self._send(303, b"", headers={"Location": "/", "Set-Cookie": cookie})
        note_failure(ip)
        return self._send(401, ui.login("کاربر یا رمز اشتباه است."))

    # -- actions ----------------------------------------------------------
    def _restart_gateway(self) -> str:
        try:
            ops.container_action("gateway", "restart")
            return ""
        except Exception as exc:  # noqa: BLE001
            return f" (ری‌استارت گیت‌وی ناموفق: {exc})"

    def _outbounds_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        count, errors = ops.add_links(
            form.get("links", ""),
            form.get("tag", ""),
            form.get("group", "cdn"),
            form.get("resolve", ""),
            form.get("via", ""),
        )
        if not count:
            return "/outbounds", "\n".join(errors) or "لینکی اضافه نشد", "err"
        ops.render_all()
        warning = self._restart_gateway()
        ops.remember("apply", f"{count} channel(s) added{warning}")
        message = f"{count} کانال اضافه شد، کانفیگ رندر شد{warning or ' و گیت‌وی ری‌استارت شد'}"
        if errors:
            message += "\n" + "\n".join(errors)
        return "/outbounds", message, "err" if errors else "ok"

    def _outbounds_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        tag = form.get("tag", "")
        if not ops.remove_channel(tag):
            return "/outbounds", f"کانال «{tag}» پیدا نشد", "err"
        ops.render_all()
        warning = self._restart_gateway()
        ops.remember("apply", f"channel {tag} removed{warning}")
        return "/outbounds", f"کانال {tag} حذف شد{warning or ' و گیت‌وی ری‌استارت شد'}", "ok"

    def _outbounds_toggle(self, form: dict[str, str]) -> tuple[str, str, str]:
        tag = form.get("tag", "")
        state = ops.toggle_channel(tag)
        if state is None:
            return "/outbounds", f"کانال «{tag}» پیدا نشد", "err"
        ops.render_all()
        warning = self._restart_gateway()
        label = "فعال" if state else "غیرفعال"
        ops.remember("apply", f"channel {tag} -> {label}{warning}")
        return "/outbounds", f"کانال {tag} {label} شد{warning}", "ok"

    def _outbounds_apply(self, form: dict[str, str]) -> tuple[str, str, str]:
        ops.render_all()
        warning = self._restart_gateway()
        ops.remember("apply", f"configs rendered{warning}")
        return "/", f"کانفیگ رندر شد{warning or ' و گیت‌وی ری‌استارت شد'}", "ok" if not warning else "err"

    def _forwarding_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            summary = ops.add_forward(
                form.get("proto", "tcp"),
                form.get("listen_ip", ""),
                form.get("listen", ""),
                form.get("target", ""),
            )
        except ValueError as exc:
            return "/forwarding", str(exc), "err"
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            ops.remember("apply", f"forward {summary} saved, apply failed: {detail}")
            return "/forwarding", f"قانون ذخیره شد اما اعمال ناموفق بود: {detail}", "err"
        ops.remember("apply", f"forward {summary} applied")
        return "/forwarding", f"فوروارد {summary} اضافه و اعمال شد", "ok"

    def _forwarding_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            index = int(form.get("index", "-1"))
        except ValueError:
            index = -1
        if not ops.remove_forward(index):
            return "/forwarding", "قانون پیدا نشد", "err"
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            return "/forwarding", f"قانون حذف شد اما اعمال ناموفق بود: {detail}", "err"
        ops.remember("apply", "forward rule removed")
        return "/forwarding", "قانون حذف و فایروال به‌روز شد", "ok"

    def _forwarding_hop(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            ops.save_hop(
                form.get("enabled") == "1",
                form.get("proto", "udp"),
                form.get("range", "20000-40000"),
                form.get("target", ""),
            )
        except ValueError as exc:
            return "/forwarding", str(exc), "err"
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            return "/forwarding", f"ذخیره شد اما اعمال ناموفق بود: {detail}", "err"
        ops.remember("apply", "hop settings saved")
        return "/forwarding", "تنظیمات پورت‌هاپینگ ذخیره و اعمال شد", "ok"

    def _forwarding_apply(self, form: dict[str, str]) -> tuple[str, str, str]:
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            ops.remember("apply", f"forwarding apply failed: {detail}")
            return "/forwarding", f"اعمال ناموفق: {detail}", "err"
        ops.remember("apply", "forwarding applied")
        return "/forwarding", "قوانین روی فایروال اعمال شد", "ok"

    def _panel_route(self, form: dict[str, str]) -> tuple[str, str, str]:
        ok, detail = ops.patch_panel_route()
        ops.remember("panel", detail)
        return "/", detail, "ok" if ok else "err"

    def _service_action(self, form: dict[str, str]) -> tuple[str, str, str]:
        service = form.get("service", "")
        action = form.get("action", "")
        if service not in {"gateway", "panel", "singbox"}:
            return "/", "سرویس نامعتبر", "err"
        if action not in {"restart", "start", "stop"}:
            return "/", "عملیات نامعتبر", "err"
        ops.container_action(service, action)
        labels = {"restart": "ری‌استارت", "start": "استارت", "stop": "استاپ"}
        return "/", f"{service}: {labels[action]} انجام شد", "ok"


def main() -> int:
    password()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    print(f"[console] listening on http://{BIND}:{PORT} user={USER}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
