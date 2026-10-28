"""Outlander web console: an authenticated, bilingual UI over the outlander operations."""

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

from . import i18n, ops, ui, peers

USER = os.environ.get("CONSOLE_USER", "admin")
PORT = int(os.environ.get("CONSOLE_PORT", "8099"))
BIND = os.environ.get("CONSOLE_BIND", "0.0.0.0")
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL", str(12 * 3600)))
MAX_BODY = 2 * 1024 * 1024
PASSWORD_FILE = Path(os.environ.get("CONSOLE_PASSWORD_FILE", "/data/console-password.txt"))
LANG_COOKIE = "ol_lang"

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
    server_version = "OutlanderConsole/1.5"
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

    def _lang(self) -> str:
        return i18n.normalise(self._cookie(LANG_COOKIE))

    def _public_host(self) -> str:
        configured = (ops.env().get("PUBLIC_HOST") or "").strip()
        if configured:
            return configured
        parsed = urllib.parse.urlsplit(f"//{self.headers.get('Host') or ''}")
        return parsed.hostname or "127.0.0.1"

    def _back(self) -> str:
        """Path of the referring page, for the language switch."""
        parsed = urllib.parse.urlparse(self.headers.get("Referer") or "")
        path = parsed.path or "/"
        if not path.startswith("/") or path.startswith("//"):
            return "/"
        return path

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

    def _context(self, token: str, params: dict[str, list[str]], include_panel: bool = True) -> dict:
        lang = self._lang()
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
                if channel.get("resolve"):
                    detail_parts.append(f"resolve={channel['resolve']}")
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
        cf = ops.cloudflare_settings()
        public_host = self._public_host()
        try:
            proxies = ops.proxy_ports(public_host) if include_panel else []
            proxy_error = ""
        except ops.OpError as exc:
            proxies = []
            proxy_error = i18n.t(lang, exc.code, **exc.params)
        except Exception as exc:  # noqa: BLE001 - the panel may be down
            proxies = []
            proxy_error = str(exc)
        return {
            "lang": lang,
            "csrf": csrf_for(token),
            "channels": channels,
            "rules": forwarding.get("rules", []),
            "peers": peers.public(ops.ROOT),
            "hop": forwarding.get("hop", {}),
            "proxy_ports": proxies,
            "proxy_host": public_host,
            "proxy_error": proxy_error,
            "cf": {"token_set": bool(cf["token"]), "email": cf["email"]},
            "states": {key: ops.container_state(key) for key in ("gateway", "panel", "singbox")},
            "state": ops.read_state(),
            "flash": ui.flash_html(params.get("flash", [""])[0], params.get("level", ["ok"])[0]),
            "socks_port": env.get("GATEWAY_SOCKS_PORT", "10808"),
        }

    # -- verbs ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")

        if path.startswith("/lang/"):
            code = i18n.normalise(path.split("/")[-1])
            cookie = f"{LANG_COOKIE}={code}; Path=/; Max-Age=31536000; SameSite=Lax"
            return self._send(303, b"", headers={"Location": self._back(), "Set-Cookie": cookie})

        if path == "/login":
            if self._session():
                return self._redirect("/")
            return self._send(200, ui.login(lang=self._lang()))

        token = self._session()
        if not token:
            return self._redirect("/login")
        context = self._context(token, params, include_panel=path != "/peers")
        if path == "/":
            return self._send(200, ui.dashboard(context))
        if path == "/outbounds":
            return self._send(200, ui.outbounds(context))
        if path == "/forwarding":
            return self._send(200, ui.forwarding(context))
        if path == "/peers":
            return self._send(200, ui.peer_servers(context))
        return self._send(404, ui.layout("404", "<h1>404</h1>", "/", "", self._lang()))

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
            "/peers/add": self._peers_add,
            "/peers/test": self._peers_test,
            "/peers/provision": self._peers_provision,
            "/peers/remove-transport": self._peers_remove_transport,
            "/peers/delete": self._peers_delete,
            "/outbounds/add": self._outbounds_add,
            "/outbounds/delete": self._outbounds_delete,
            "/outbounds/toggle": self._outbounds_toggle,
            "/outbounds/apply": self._outbounds_apply,
            "/forwarding/add": self._forwarding_add,
            "/forwarding/update": self._forwarding_update,
            "/forwarding/delete": self._forwarding_delete,
            "/forwarding/hop": self._forwarding_hop,
            "/forwarding/apply": self._forwarding_apply,
            "/proxy/add": self._proxy_add,
            "/proxy/delete": self._proxy_delete,
            "/cloudflare/save": self._cloudflare_save,
            "/certs/renew": self._certs_renew,
            "/panel/route": self._panel_route,
            "/service/action": self._service_action,
        }
        handler = routes.get(path)
        if not handler:
            return self._send(404, "not found", "text/plain; charset=utf-8")
        try:
            target, message, level = handler(form)
        except peers.PeerError as exc:
            target, message, level = "/peers" if path.startswith("/peers/") else "/forwarding", str(exc), "err"
        except ops.OpError as exc:
            target, message, level = "/", i18n.t(self._lang(), exc.code, **exc.params), "err"
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            target, message, level = "/", f"{type(exc).__name__}: {exc}", "err"
        self._redirect(target, message, level)

    # -- auth -------------------------------------------------------------
    def _login(self, form: dict[str, str]) -> None:
        lang = self._lang()
        ip = self.client_address[0]
        if not login_allowed(ip):
            return self._send(429, ui.login(i18n.t(lang, "login.rate"), lang))
        if form.get("user", "") == USER and _safe_equal(form.get("password", ""), password()):
            token = new_session()
            cookie = f"ol_console={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}"
            return self._send(303, b"", headers={"Location": "/", "Set-Cookie": cookie})
        note_failure(ip)
        return self._send(401, ui.login(i18n.t(lang, "login.bad"), lang))

    # -- actions ----------------------------------------------------------
    def _tr(self, key: str, **params: object) -> str:
        return i18n.t(self._lang(), key, **params)

    def _restart_gateway(self) -> str:
        try:
            ops.container_action("gateway", "restart")
            return ""
        except Exception as exc:  # noqa: BLE001
            return str(exc)

    def _outbounds_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        count, errors = ops.add_links(
            form.get("links", ""),
            form.get("tag", ""),
            form.get("group", "cdn"),
            form.get("resolve", ""),
            form.get("via", ""),
        )
        if not count:
            detail = "\n".join(errors) or self._tr("flash.nothing")
            return "/outbounds", self._tr("flash.links_invalid") + "\n" + detail, "err"
        ops.render_all()
        warning = self._restart_gateway()
        if warning:
            message = self._tr("flash.channel_added_warn", count=count, error=warning)
            level = "err"
        else:
            message = self._tr("flash.channel_added", count=count)
            level = "ok"
        ops.remember("apply", message)
        if errors:
            message += "\n" + "\n".join(errors)
        return "/outbounds", message, level

    def _outbounds_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        tag = form.get("tag", "")
        if not ops.remove_channel(tag):
            return "/outbounds", self._tr("flash.channel_missing", tag=tag), "err"
        ops.render_all()
        warning = self._restart_gateway()
        if warning:
            message = self._tr("flash.channel_removed_warn", tag=tag, error=warning)
            level = "err"
        else:
            message = self._tr("flash.channel_removed", tag=tag)
            level = "ok"
        ops.remember("apply", message)
        return "/outbounds", message, level

    def _outbounds_toggle(self, form: dict[str, str]) -> tuple[str, str, str]:
        tag = form.get("tag", "")
        state = ops.toggle_channel(tag)
        if state is None:
            return "/outbounds", self._tr("flash.channel_missing", tag=tag), "err"
        ops.render_all()
        warning = self._restart_gateway()
        label = self._tr("common.enabled" if state else "common.disabled")
        message = self._tr("flash.channel_toggled", tag=tag, state=label)
        if warning:
            message += f" ({warning})"
        ops.remember("apply", message)
        return "/outbounds", message, "err" if warning else "ok"

    def _outbounds_apply(self, form: dict[str, str]) -> tuple[str, str, str]:
        ops.render_all()
        warning = self._restart_gateway()
        if warning:
            message = self._tr("flash.rendered_warn", error=warning)
            level = "err"
        else:
            message = self._tr("flash.rendered")
            level = "ok"
        ops.remember("apply", message)
        return "/", message, level

    def _forwarding_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        if form.get("mode") == "auto":
            summary = ops.add_auto_forward(form)
            return "/forwarding", self._tr("flash.forward_added", summary=summary), "ok"
        if form.get("mode") == "peer":
            summary = ops.add_peer_forward(form)
            return "/forwarding", self._tr("flash.forward_added", summary=summary), "ok"
        summary = ops.add_forward(
            form.get("proto", "tcp"),
            form.get("listen_ip", ""),
            form.get("listen", ""),
            form.get("target", ""),
            form.get("mode", "nft"),
        )
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            ops.remember("apply", f"forward {summary} saved, apply failed: {detail}")
            return "/forwarding", self._tr("flash.forward_add_failed", error=detail), "err"
        tunnel_ok, tunnel_detail = ops.apply_tunnel()
        message = self._tr("flash.forward_added", summary=summary)
        if not tunnel_ok:
            message += f" ({tunnel_detail})"
        ops.remember("apply", message)
        return "/forwarding", message, "ok" if tunnel_ok else "err"

    def _forwarding_update(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            index = int(form.get("index", "-1"))
        except ValueError:
            index = -1
        summary = ops.update_forward(index, form.get("rule_token", ""), form)
        return "/forwarding", self._tr("fw.updated", summary=summary), "ok"

    def _forwarding_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            index = int(form.get("index", "-1"))
        except ValueError:
            index = -1
        rules = ops.forwarding()["rules"]
        if 0 <= index < len(rules) and ops.forward.effective_mode(rules[index]) == "peer":
            ops.remove_peer_forward(index)
            return "/forwarding", self._tr("flash.forward_removed"), "ok"
        if not ops.remove_forward(index):
            return "/forwarding", self._tr("flash.rule_missing"), "err"
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            return "/forwarding", self._tr("flash.forward_remove_failed", error=detail), "err"
        tunnel_ok, tunnel_detail = ops.apply_tunnel()
        message = self._tr("flash.forward_removed")
        if not tunnel_ok:
            message += f" ({tunnel_detail})"
        ops.remember("apply", message)
        return "/forwarding", message, "ok" if tunnel_ok else "err"

    def _peers_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        peers.save_server(ops.ROOT, form)
        return "/peers", self._tr("peer.saved"), "ok"

    def _peers_test(self, form: dict[str, str]) -> tuple[str, str, str]:
        peers.start_job(ops.ROOT, form.get("peer_id", ""), "test")
        return "/peers", self._tr("peer.started"), "ok"

    def _peers_provision(self, form: dict[str, str]) -> tuple[str, str, str]:
        peers.start_job(ops.ROOT, form.get("peer_id", ""), "provision", kind=form.get("transport", ""),
                        port=form.get("tunnel_port", ""), sni=form.get("sni", ""))
        return "/peers", self._tr("peer.started"), "ok"

    def _peers_remove_transport(self, form: dict[str, str]) -> tuple[str, str, str]:
        peers.remove_transport(ops.ROOT, form.get("peer_id", ""), form.get("transport", ""))
        return "/peers", self._tr("peer.removed"), "ok"

    def _peers_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        peers.delete_server(ops.ROOT, form.get("peer_id", ""))
        return "/peers", self._tr("peer.removed"), "ok"

    def _forwarding_hop(self, form: dict[str, str]) -> tuple[str, str, str]:
        ops.save_hop(
            form.get("enabled") == "1",
            form.get("proto", "udp"),
            form.get("range", "20000-40000"),
            form.get("target", ""),
        )
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            return "/forwarding", self._tr("flash.hop_failed", error=detail), "err"
        message = self._tr("flash.hop_saved")
        ops.remember("apply", message)
        return "/forwarding", message, "ok"

    def _forwarding_apply(self, form: dict[str, str]) -> tuple[str, str, str]:
        ops.render_all()
        ok, detail = ops.apply_forwarding()
        if not ok:
            ops.remember("apply", f"forwarding apply failed: {detail}")
            return "/forwarding", self._tr("flash.fwd_failed", error=detail), "err"
        message = self._tr("flash.fwd_applied")
        ops.remember("apply", message)
        return "/forwarding", message, "ok"

    def _proxy_add(self, form: dict[str, str]) -> tuple[str, str, str]:
        raw_port = (form.get("port") or "").strip()
        if not raw_port.isdigit():
            return "/forwarding", self._tr("err.proxy_busy", ports=raw_port or "?"), "err"
        result = ops.add_proxy_port(
            int(raw_port),
            form.get("remark", ""),
            form.get("kind", "reality"),
            self._public_host(),
            domain=form.get("domain", ""),
            email=form.get("email", ""),
            want_cert=form.get("cert") == "1",
        )
        message = self._tr("flash.proxy_created", port=result["port"], link=result["link"])
        ops.remember("proxy", message)
        return "/forwarding", message, "ok"

    def _proxy_delete(self, form: dict[str, str]) -> tuple[str, str, str]:
        try:
            inbound_id = int(form.get("id", "-1"))
        except ValueError:
            inbound_id = -1
        ops.delete_proxy_port(inbound_id)
        message = self._tr("flash.proxy_deleted")
        ops.remember("proxy", message)
        return "/forwarding", message, "ok"

    def _cloudflare_save(self, form: dict[str, str]) -> tuple[str, str, str]:
        updates: dict[str, str] = {}
        token = (form.get("token") or "").strip()
        if token:
            updates["CF_API_TOKEN"] = token
        updates["CF_EMAIL"] = (form.get("email") or "").strip()
        ops.save_env(updates)
        message = self._tr("flash.cf_saved")
        ops.remember("certificates", message)
        return "/forwarding", message, "ok"

    def _certs_renew(self, form: dict[str, str]) -> tuple[str, str, str]:
        ok, detail = ops.renew_certificates()
        if ok:
            message = self._tr("flash.certs_renewed", detail=detail)
        else:
            message = self._tr("flash.certs_renew_failed", detail=detail)
        ops.remember("certificates", message)
        return "/forwarding", message, "ok" if ok else "err"

    def _panel_route(self, form: dict[str, str]) -> tuple[str, str, str]:
        ok, detail = ops.patch_panel_route()
        if ok:
            message = self._tr("flash.panel_routed", outbounds=detail)
        else:
            message = self._tr("flash.panel_failed", error=detail)
        ops.remember("panel", message)
        return "/", message, "ok" if ok else "err"

    def _service_action(self, form: dict[str, str]) -> tuple[str, str, str]:
        service = form.get("service", "")
        action = form.get("action", "")
        if service not in {"gateway", "panel", "singbox"}:
            return "/", self._tr("flash.bad_service"), "err"
        if action not in {"restart", "start", "stop"}:
            return "/", self._tr("flash.bad_action"), "err"
        ops.container_action(service, action)
        labels = {
            "restart": self._tr("dash.restart"),
            "start": self._tr("dash.start"),
            "stop": self._tr("dash.stop"),
        }
        return "/", self._tr("flash.service_done", service=service, action=labels[action]), "ok"


def main() -> int:
    password()
    ops.start_auto_worker()
    peers.start_worker(ops.ROOT)
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
