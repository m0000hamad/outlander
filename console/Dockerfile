FROM python:3.12-slim-trixie

# Use Debian's Certbot and plugin together; building must not depend on PyPI.
RUN install_packages() { \
      apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 -o APT::Update::Error-Mode=any update \
      && apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 install -y --no-install-recommends \
         nftables iptables ca-certificates certbot python3-certbot-dns-cloudflare openssh-client sshpass openssl; \
    }; \
    if ! install_packages; then \
      echo "Primary Debian repositories failed; retrying with fallback mirrors" >&2; \
      sed -i 's|deb.debian.org/debian-security|security.debian.org/debian-security|g; s|deb.debian.org/debian|ftp.debian.org/debian|g' /etc/apt/sources.list.d/debian.sources; \
      rm -rf /var/lib/apt/lists/*; \
      install_packages || exit 1; \
    fi \
    && certbot --version \
    && certbot plugins --text | grep -q 'dns-cloudflare' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OUTLANDER_ROOT=/repo \
    CONSOLE_PORT=8099

EXPOSE 8099

CMD ["python3", "-m", "console"]
