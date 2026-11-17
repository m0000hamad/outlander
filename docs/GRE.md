# GRE forwarding

Select **GRE tunnel** beside Dokodemo on the Forwarding page. GRE carries TCP
and UDP over a Linux IPv4 tunnel. It is an explicit manual path: Auto continues
to select encrypted peer/gateway routes and does not select GRE.

The console configures this server. Configure the reverse tunnel on the remote
server first. The destination service must listen on the remote **inner tunnel
address** (or all addresses). This mode forwards to that address; it does not
route arbitrary remote subnets or change the host's default route.

Example addresses (replace the documentation outer IPs with real addresses):

- Local outer IPv4: `203.0.113.1` — an address assigned to this server.
- Remote outer IPv4: `198.51.100.1` — the remote server address.
- Local tunnel IPv4: `10.255.0.1` — an unused inner address.
- Target: `10.255.0.2:443` — the remote inner address and service port.
- Listen: `28443`, protocol: `both` — client TCP/UDP traffic enters here.

On the remote server:

```bash
sudo ip tunnel add ol-gre mode gre local 198.51.100.1 remote 203.0.113.1 ttl 64
sudo ip address add 10.255.0.2/32 peer 10.255.0.1/32 dev ol-gre
sudo ip link set dev ol-gre mtu 1400 up
```

Persist the remote configuration with that server's network manager. Permit
**IP protocol 47** between the two outer addresses on both hosts and provider
firewalls; this is not TCP/UDP port 47. Permit the destination service through
the remote firewall. GRE provides no encryption and does not pass through a CDN.
The Linux kernel must support `ip_gre`; load it on the host if necessary.
NAT or a provider blocking protocol 47 may prevent this mode from working.

The same local configuration from the CLI:

```bash
python3 -m outlander forward add --mode gre --proto both --listen 28443 \
  --target 10.255.0.2:443 --gre-local 203.0.113.1 \
  --gre-remote 198.51.100.1 --gre-address 10.255.0.1
sudo bash scripts/forward-apply.sh
```

Port ranges and whole-IP forwarding follow the existing DNAT rules: omit the
target port to preserve ports; `--listen '*'` also requires `--listen-ip`.
Compatible rules share one owned interface. All rules with the same outer
endpoints must use the same inner addresses. Each remote inner address must
identify a single tunnel. Do not reuse an outer endpoint as an inner address.
Deleting the final rule removes its owned interface; unrelated links remain.
Masquerading gives replies a return path through the local inner address.

After updating an existing installation, rebuild the console for `iproute2`:

```bash
docker compose up -d --build console
```

Fresh installs restore GRE with `outlander-forward.service`. On existing hosts,
add this drop-in once so the old installed unit also restores GRE after reboot:

```bash
sudo systemctl edit outlander-forward.service
```

```ini
[Service]
WorkingDirectory=/absolute/path/to/outlander
ExecStartPre=/usr/bin/python3 -m outlander.gre
```

Use the real installation path, then run `sudo systemctl daemon-reload`.
The console's Add/Edit operations restore the previous settings on apply
failure. Applying an interface verifies local configuration, not remote service
reachability; check `ping 10.255.0.2` and the destination service separately.

Reference: [upstream iproute2 tunnel documentation](https://github.com/iproute2/iproute2/blob/main/man/man8/ip-tunnel.8).

## راه‌اندازی فارسی

در صفحه فوروارد، مسیر **تونل GRE** را انتخاب کنید. IP بیرونی این سرور و
سرور مقابل و IP داخلی تونل این سرور را وارد کنید. مقصد باید IP داخلی تونل
سرور مقابل باشد؛ مثلاً `10.255.0.2:443`. سمت مقابل را با دستورهای بالا
به‌صورت معکوس تنظیم و پایدار کنید. سرویس مقصد باید روی IP داخلی یا همه
آدرس‌ها گوش کند. پروتکل IP شماره ۴۷ و دسترسی به سرویس مقصد را در فایروال
مجاز کنید. GRE رمزنگاری ندارد، از CDN عبور نمی‌کند و وارد انتخاب خودکار
مسیر نمی‌شود. بعد از آپدیت، کانتینر کنسول را بازسازی کنید و برای نصب‌های
قبلی drop-in بالا را با مسیر واقعی پروژه اضافه کنید.
