FROM python:3.12-slim-trixie

# Use Debian's Certbot and plugin together; building must not depend on PyPI.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
       nftables iptables ca-certificates certbot python3-certbot-dns-cloudflare openssh-client sshpass openssl \
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
