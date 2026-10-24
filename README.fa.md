# Outlander

زیرساخت پروکسی زنجیره‌ای برای یک سرور Ubuntu: پنل مدیریت شبیه **ثنایی/3x-ui**، یک **گیت‌وی Xray** که ترافیک را از اوتباندهای CDN رد می‌کند (مثل لینک VLESS+WS که دادید)، پروتکل‌های **Hysteria2 / TUIC** با sing-box، و **فورواردینگ پورت و IP** با nftables و HAProxy.

```
کاربر
  │  VLESS+REALITY / VLESS+WS+TLS / Trojan / SS-2022 / Hysteria2 / TUIC
  ▼
┌──────────────┐        ┌──────────────────┐        ┌──────────────┐
│  edge        │ ─────► │  gateway (Xray)  │ ─────► │  CDN outbound│ ──► اینترنت
│ 3x-ui +      │  socks │  balancer +      │        │  vless://... │
│ sing-box     │ :10808 │  health-check    │        └──────────────┘
└──────────────┘        │  + direct (داخلی)│
                        └──────────────────┘
```

- **edge**: پنل 3x-ui (پورت ۲۰۵۳) + اینباندهای کلاینت + sing-box برای Hysteria2/TUIC.
- **gateway**: یک Xray مستقل که فقط اوتباند است. همهٔ کانال‌ها با `leastPing` بالانس می‌شوند و اگر همه بخوابند `direct` می‌شود.
- **رندر کانفیگ**: هیچ کانفیگی دستی نوشته نمی‌شود؛ از `config/inventory.json` ساخته می‌شود تا لینک‌ها و UUIDها داخل گیت لو نروند.

---

## ۱) نصب

روی Ubuntu 22.04 یا 24.04 (root):

```bash
git clone <your-repo-url> outlander
cd outlander
sudo bash scripts/install.sh --outbound 'vless://<uuid>@abr02.example.ir:80?encryption=none&security=none&type=ws&host=link.example-cdn.ir&path=%2Flink%2F<token>#CDN2'
```

اسکریپت این کارها را انجام می‌دهد: نصب Docker، تنظیم BBR، ساخت `.env` با پسوردهای تصادفی، افزودن اوتباند، رندر کانفیگ، بالا آوردن استک، ست کردن یوزر/پس پنل و **وصل کردن پنل به گیت‌وی**.

در پایان آدرس پنل، یوزرنیم و پسورد چاپ می‌شود. بعد از آن:

```bash
make doctor     # چک سرور و سرویس‌ها
make ps
```

بدون اوتباند هم می‌شود نصب کرد و بعداً اضافه کرد.

---

## ۲) مدیریت اوتباندها

```bash
python3 -m outlander add '<link>' --tag starlink-cdn2 --group cdn
python3 -m outlander list
python3 -m outlander rm starlink-cdn2
python3 -m outlander render && docker compose restart gateway
```

| گزینه | کاربرد |
|---|---|
| `--tag` | نام کوتاه کانال (در بالانسر و لاگ‌ها دیده می‌شود) |
| `--group` | گروه کانال؛ برای هر گروه یک بالانسر جدا ساخته می‌شود (`ol-bal-<group>`) |
| `--resolve 1.2.3.4` | اگر DNS دامنهٔ CDN مسموم بود، IP را پین می‌کند |
| `--via <tag>` | زنجیره: اول از کانال دیگر رد شود، بعد این کانال |

پروتکل‌های پشتیبانی‌شده برای اوتباند: `vless`, `vmess`, `trojan`, `ss`, `socks`, `http`, `hysteria2`, `tuic`.
Xray پروتکل‌های اول را خودش می‌زند؛ Hysteria2/TUIC در sing-box اجرا می‌شوند و با یک SOCKS محلی (با پسورد) به گیت‌وی وصل می‌شوند.

جزئیات مسیر WS، هدر `Host`، `path`، REALITY (`pbk`, `sid`, `fp`) و TLS خودکار از لینک خوانده می‌شود.

بعد از هر تغییر: `render` و `docker compose restart gateway`.

---

## ۳) اینباندهای پنل

پنل روی پورت `2053` بالا می‌آید (پنل در حالت host network است تا IP واقعی کلاینت را ببیند).

| پروتکل | پورت | نکته |
|---|---|---|
| VLESS + REALITY | 443/tcp | `dest`: یک سایت واقعی مثل `www.microsoft.com:443`، `flow`: `xtls-rprx-vision` |
| VLESS + WS | 8080/tcp | بدون TLS، برای پشت CDN (TLS روی CDN) |
| VLESS + WS + TLS | 8443/tcp | با سرتیفیکیت خودتان، برای CDN در حالت Full |
| Trojan | 2087/tcp | با سرتیفیکیت |
| Shadowsocks-2022 | 8388/tcp+udp | سریع، بدون TLS handshake |
| Hysteria2 | 8443/udp | در sing-box، با obfs salamander و port hopping |
| TUIC | 8444/udp | در sing-box |

مرحله‌به‌مرحله در [docs/PANEL.md](docs/PANEL.md).

> ترافیک همهٔ این اینباندها به‌صورت خودکار از گیت‌وی (اوتباندهای CDN) بیرون می‌رود. اگر در پنل تنظیمات Xray را عوض کردید یا پنل را آپدیت کردید، یک‌بار `make panel` بزنید.

---

## ۴) CDN (مخفی کردن IP سرور)

۱. دامنه را در CDN (Cloudflare / CloudyFa / ArvanCloud) اضافه کنید، رکورد `A` به IP سرور با پروکسی روشن.
۲. پورت مبدأ (origin) را `8080` (بدون TLS) یا `8443` (Full) بگذارید.
۳. WebSocket را فعال کنید؛ `Host` کلاینت باید دامنهٔ CDN باشد و `path` مثل `/ol`.
۴. برای اینکه IP سرور مستقیم جواب ندهد:

```bash
sudo bash scripts/cdn-firewall.sh          # فقط رنج‌های Cloudflare به 80/8080/8443
sudo ADMIN_IPS='1.2.3.4' bash scripts/cdn-firewall.sh   # IP خودتان هم مجاز
```

REALITY را هرگز پشت CDN نگذارید؛ REALITY باید مستقیم روی IP باشد.

جزئیات: [docs/CDN.md](docs/CDN.md)

---

## ۵) فورواردینگ پورت و IP

**پورت‌هاپینگ Hysteria2** (کلاینت روی محدودهٔ پورت وصل می‌شود، سرور همه را به ۸۴۴۳ می‌فرستد):

```bash
python3 -m outlander forward render
sudo bash scripts/forward-apply.sh
```

**فوروارد دلخواه L4:**

```bash
python3 -m outlander forward add --proto tcp --listen 3389 --target 10.0.0.5:3389
python3 -m outlander forward add --proto both --listen 50000-50100 --target 172.28.60.12
sudo bash scripts/forward-apply.sh
python3 -m outlander forward list
```

**فوروارد بر اساس دامنه/SNI** (چند دامنه روی یک IP، هر کدام به بک‌اند خودش): `config/haproxy/haproxy.cfg` را ویرایش کنید و با `COMPOSE_PROFILES=ha` بالا بیاورید.

جزئیات: [docs/FORWARDING.md](docs/FORWARDING.md)

---

## ۶) Hysteria2 و سرتیفیکیت

```bash
sudo bash scripts/cert.sh hy2.example.com     # certbot + نصب در certs/
# در .env: HY2_ENABLED=1 و HY2_SNI=hy2.example.com
python3 -m outlander render && docker compose --profile extra up -d
```

کلاینت: `hysteria2://<HY2_PASSWORD>@<ip>:8443?sni=hy2.example.com&obfs=salamander&obfs-password=<HY2_OBFS_PASSWORD>&mport=20000-40000`

---

## ۷) دستورهای روزمره

```bash
make up | down | restart | logs | ps
make test        # یونیت‌تست + xray -test روی کانفیگ رندرشده
make doctor
make backup      # .env + config + دیتابیس پنل + سرتیفیکیت‌ها
make panel
```

- لاگ‌ها: `docker compose logs -f gateway`
- آپدیت: `docker compose pull && docker compose up -d`
- بکاپ روزانه: `scripts/backup.sh` را در cron بگذارید.

[docs/OPERATIONS.md](docs/OPERATIONS.md)

---

## ساختار

```
outlander/
├── outlander/            # CLI و رندر کانفیگ (بدون وابستگی، فقط CPython)
├── config/
│   ├── inventory.json    # کانال‌ها (gitignore)
│   ├── xray/config.json  # گیت‌وی (gitignore، ساخته می‌شود)
│   ├── sing-box/config.json
│   ├── haproxy/haproxy.cfg
│   └── nftables/
├── scripts/              # install, cert, firewall, backup, forward
├── tests/
└── docs/
```

`.env`، `inventory.json`، کانفیگ‌های رندرشده، `data/` و `certs/` عمداً در `.gitignore` هستند: لینک‌های اوتباند شما رمز محسوب می‌شوند.

---

## نکته

این ابزار برای دسترسی به اینترنت آزاد و مدیریت سرور خودتان است. مسئولیت رعایت قوانین محلی و شرایط سرویس ارائه‌دهنده (سرور و CDN) با شماست.
