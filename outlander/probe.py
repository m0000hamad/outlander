"""Reachability probes used by the smart forward mode."""

from __future__ import annotations

import socket
import struct
import ssl
import urllib.parse


def _read_exact(sock, size: int) -> bytes:
    data = b""
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise OSError("SOCKS connection closed")
        data += part
    return data


def _socks_connect(proxy_host: str, proxy_port: int, host: str, port: int, timeout: float):
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        if _read_exact(sock, 2) != b"\x05\x00":
            raise OSError("SOCKS authentication rejected")
        try:
            address = b"\x01" + socket.inet_aton(host)
        except OSError:
            name = host.encode("idna")
            if len(name) > 255:
                raise OSError("Target hostname too long")
            address = b"\x03" + bytes([len(name)]) + name
        sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", port))
        reply = _read_exact(sock, 4)
        if reply[:2] != b"\x05\x00":
            raise OSError("SOCKS CONNECT rejected")
        if reply[3] == 1:
            _read_exact(sock, 6)
        elif reply[3] == 4:
            _read_exact(sock, 18)
        elif reply[3] == 3:
            _read_exact(sock, _read_exact(sock, 1)[0] + 2)
        else:
            raise OSError("Invalid SOCKS reply")
        return sock
    except Exception:
        sock.close()
        raise


def socks5_http_reachable(proxy_host: str, proxy_port: int, url: str, timeout: float = 6.0) -> bool:
    """Require an actual HTTP response through the authenticated tunnel, not a SOCKS ACK."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with _socks_connect(proxy_host, proxy_port, parsed.hostname, port, timeout) as raw:
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname) if parsed.scheme == "https" else raw
            try:
                path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                host = parsed.hostname + (f":{port}" if port not in {80, 443} else "")
                sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
                with sock.makefile("rb") as response:
                    status = response.readline(1024).split()
                return len(status) >= 2 and status[0].startswith(b"HTTP/") and 200 <= int(status[1]) < 400
            finally:
                if sock is not raw:
                    sock.close()
    except (OSError, ValueError, UnicodeError):
        return False


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """True when a TCP connection to host:port succeeds from this host."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def socks5_reachable(
    proxy_host: str,
    proxy_port: int,
    host: str,
    port: int,
    timeout: float = 6.0,
) -> bool:
    """True when a SOCKS5 proxy can CONNECT to host:port (no auth)."""
    try:
        with _socks_connect(proxy_host, proxy_port, host, port, timeout):
            return True
    except (OSError, UnicodeError):
        return False
