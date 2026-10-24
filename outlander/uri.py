"""Parse proxy share links (vless, vmess, trojan, ss, socks, http, hysteria2, tuic).

Everything here is dependency free so the CLI runs on a bare Ubuntu host with
nothing but CPython installed.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.parse
from dataclasses import dataclass, field

TRUTHY = {"1", "true", "yes", "on"}

XRAY_SCHEMES = {"vless", "vmess", "trojan", "ss", "socks", "http"}
SINGBOX_SCHEMES = {"hysteria2", "tuic"}

ALIASES = {"hy2": "hysteria2", "shadowsocks": "ss"}

SS_METHODS = {
    "none",
    "plain",
    "rc4-md5",
    "aes-128-cfb",
    "aes-192-cfb",
    "aes-256-cfb",
    "aes-128-gcm",
    "aes-192-gcm",
    "aes-256-gcm",
    "chacha20-ietf",
    "chacha20-ietf-poly1305",
    "chacha20-poly1305",
    "xchacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
}

SCHEME_PORTS = {
    "vless": 443,
    "vmess": 443,
    "trojan": 443,
    "ss": 8388,
    "socks": 1080,
    "http": 8080,
    "hysteria2": 443,
    "tuic": 443,
}


class LinkError(ValueError):
    """Raised when a share link cannot be understood."""


def truthy(value: object) -> bool:
    return str(value).strip().lower() in TRUTHY


def _b64(value: str) -> str:
    text = value.strip().replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text).decode("utf-8", "replace")
    except (binascii.Error, ValueError) as exc:
        raise LinkError(f"invalid base64 payload: {exc}") from exc


@dataclass
class Link:
    scheme: str
    address: str
    port: int
    uuid: str = ""
    password: str = ""
    method: str = ""
    flow: str = ""
    name: str = ""
    params: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    @property
    def is_singbox(self) -> bool:
        return self.scheme in SINGBOX_SCHEMES

    @property
    def network(self) -> str:
        return (self.params.get("type") or "tcp").lower()

    @property
    def security(self) -> str:
        return (self.params.get("security") or "none").lower()

    @property
    def host(self) -> str:
        return self.params.get("host") or ""

    @property
    def path(self) -> str:
        return self.params.get("path") or "/"

    @property
    def sni(self) -> str:
        return (
            self.params.get("sni")
            or self.params.get("peer")
            or self.params.get("servername")
            or self.host
            or self.address
        )

    def describe(self) -> str:
        label = self.name or self.address
        return f"{self.scheme}://{self.address}:{self.port} ({label})"


def _params(query: str) -> dict[str, str]:
    if not query:
        return {}
    parsed = urllib.parse.parse_qs(query, keep_blank_values=True)
    out: dict[str, str] = {}
    for key, values in parsed.items():
        out[key.strip().lower()] = urllib.parse.unquote(values[0]).strip()
    return out


def _split_host_port(netloc: str, scheme: str) -> tuple[str, int]:
    host_port = netloc.rsplit("@", 1)[-1]
    parts = urllib.parse.urlsplit(f"//{host_port}")
    host = parts.hostname
    if not host:
        raise LinkError(f"{scheme}: missing host")
    try:
        port = parts.port
    except ValueError as exc:
        raise LinkError(f"{scheme}: invalid port: {exc}") from exc
    return host, port or SCHEME_PORTS.get(scheme, 0)


def _split_body(raw: str, scheme: str) -> tuple[str, str, str]:
    body = raw[len(scheme) + 3 :]
    body, _, fragment = body.partition("#")
    body, _, query = body.partition("?")
    return body, query, urllib.parse.unquote(fragment)


def _parse_vmess_json(raw: str) -> Link:
    payload = _b64(raw[len("vmess://") :])
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LinkError(f"vmess: payload is not JSON: {exc}") from exc
    if not isinstance(data, dict) or "add" not in data:
        raise LinkError("vmess: payload is missing the 'add' field")
    params = {
        "type": str(data.get("net") or "tcp"),
        "security": (str(data.get("tls") or "none")).lower(),
        "scy": str(data.get("scy") or "auto"),
        "aid": str(data.get("aid") or 0),
        "encryption": "none",
    }
    for key, target in (("host", "host"), ("path", "path"), ("sni", "sni"), ("alpn", "alpn"), ("fp", "fp")):
        if data.get(key):
            params[target] = str(data[key])
    if data.get("port"):
        params.setdefault("port", str(data["port"]))
    return Link(
        scheme="vmess",
        address=str(data["add"]),
        port=int(data.get("port") or 443),
        uuid=str(data.get("id") or ""),
        name=str(data.get("ps") or ""),
        params=params,
        raw=raw,
    )


def _parse_ss(raw: str) -> Link:
    body, query, name = _split_body(raw, "ss")
    params = _params(query)
    if "@" in body:
        userinfo, _, hostport = body.rpartition("@")
        decoded = urllib.parse.unquote(userinfo)
        if ":" not in decoded:
            decoded = _b64(decoded)
        if ":" not in decoded:
            raise LinkError("ss: userinfo must be method:password")
        method, _, password = decoded.partition(":")
        host, port = _split_host_port(hostport, "ss")
    else:
        decoded = _b64(urllib.parse.unquote(body))
        if "@" not in decoded:
            raise LinkError("ss: payload must be method:password@host:port")
        creds, _, hostport = decoded.rpartition("@")
        method, _, password = creds.partition(":")
        host, port = _split_host_port(hostport, "ss")
    method = method.strip().lower()
    if method not in SS_METHODS:
        raise LinkError(f"ss: unsupported cipher '{method}'")
    return Link(
        scheme="ss",
        address=host,
        port=port,
        password=password,
        method=method,
        name=name,
        params=params,
        raw=raw,
    )


def parse(raw: str) -> Link:
    """Turn a share link into a Link, raising LinkError on bad input."""
    text = (raw or "").strip()
    if not text:
        raise LinkError("empty link")
    if "://" not in text:
        raise LinkError("link must start with scheme://")
    scheme = text.split("://", 1)[0].strip().lower()
    scheme = ALIASES.get(scheme, scheme)
    if scheme not in XRAY_SCHEMES | SINGBOX_SCHEMES:
        raise LinkError(f"unsupported scheme '{scheme}'")
    if scheme == "vmess" and "@" not in text.split("://", 1)[1].split("#", 1)[0]:
        return _parse_vmess_json(text)
    if scheme == "ss":
        return _parse_ss(text)

    body, query, name = _split_body(text, text.split("://", 1)[0].strip().lower())
    params = _params(query)
    if "@" not in body:
        raise LinkError(f"{scheme}: link has no user info")
    userinfo, _, hostport = body.rpartition("@")
    userinfo = urllib.parse.unquote(userinfo)
    host, port = _split_host_port(hostport, scheme)

    link = Link(scheme=scheme, address=host, port=port, name=name, params=params, raw=text)
    if scheme in {"vless", "vmess"}:
        link.uuid = userinfo
        link.flow = params.get("flow", "")
    elif scheme == "trojan":
        link.password = userinfo
        link.flow = params.get("flow", "")
    elif scheme in {"hysteria2", "tuic"}:
        if scheme == "tuic" and ":" in userinfo:
            link.uuid, _, link.password = userinfo.partition(":")
        else:
            link.password = userinfo
    elif scheme in {"socks", "http"}:
        if ":" in userinfo:
            link.uuid, _, link.password = userinfo.partition(":")
        else:
            link.uuid = userinfo
    if scheme == "vless" and not link.uuid:
        raise LinkError("vless: missing uuid")
    return link


def parse_many(raw_links: list[str]) -> list[Link]:
    return [parse(item) for item in raw_links]
