"""Reachability probes used by the smart forward mode."""

from __future__ import annotations

import socket
import struct


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
        with socket.create_connection((proxy_host, proxy_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            greeting = sock.recv(2)
            if len(greeting) < 2 or greeting[0] != 5 or greeting[1] != 0:
                return False

            try:
                address = b"\x01" + socket.inet_aton(host)
            except OSError:
                name = host.encode("idna")[:255]
                address = b"\x03" + bytes([len(name)]) + name
            sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", port))

            reply = sock.recv(10)
            return len(reply) >= 2 and reply[0] == 5 and reply[1] == 0
    except OSError:
        return False
