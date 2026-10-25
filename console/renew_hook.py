"""certbot deploy hook: install renewed certificates and reload the panel."""

from __future__ import annotations

from . import ops


def main() -> int:
    count = ops.install_all_certificates()
    print(f"installed {count} certificate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
