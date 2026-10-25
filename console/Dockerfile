FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nftables iptables ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir certbot certbot-dns-cloudflare

WORKDIR /repo
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OUTLANDER_ROOT=/repo \
    CONSOLE_PORT=8099

EXPOSE 8099

CMD ["python3", "-m", "console"]
