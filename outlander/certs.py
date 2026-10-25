"""Certificate helpers shared by the console and the shell scripts."""

from __future__ import annotations

import re
from pathlib import Path

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalise_domain(value: str) -> str:
    return (value or "").strip().lower().rstrip(".")


def valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.match(normalise_domain(value)))


def panel_cert_paths(domain: str) -> tuple[str, str]:
    """Paths as seen inside the panel container (./certs is mounted at /root/cert)."""
    domain = normalise_domain(domain)
    return (f"/root/cert/{domain}/fullchain.pem", f"/root/cert/{domain}/privkey.pem")


def host_cert_dir(root: Path, domain: str) -> Path:
    return root / "certs" / normalise_domain(domain)
