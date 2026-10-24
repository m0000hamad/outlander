# Outlander

زیرساخت پروکسی زنجیره‌ای برای یک سرور Ubuntu: **کنسول وب** برای مدیریت، پنل **3x-ui** شبیه ثنایی، یک **گیت‌وی Xray** که ترافیک را از اوتباندهای CDN رد می‌کند، پروتکل‌های **Hysteria2 / TUIC** با sing-box، و **فورواردینگ پورت و IP** با nftables و HAProxy.

```
مرورگر ──► کنسول وب (:8099) ──┐  افزودن اوتباند / فوروارد
                              ▼
کاربر ──(VLESS+REALITY / WS+TLS / Trojan / SS / Hy2 / TUIC)──► edge (3x-ui + sing-box)
                                                                     │
                                                                     ▼
                                                       gateway (Xray: balancer + health-check)
                                                         │           │            │
                                                      CDN #1      CDN #2      direct (داخلی)
```

- **کنسول وب** (`ol-console`): همهٔ کارهای روزمره با مرورگر — چسباندن لینک اوتباند، افزودن فوروارد، اعمال فوری روی سرور.
- **edge**: پنل 3x-ui (پورت ۲۰۵۳) + اینباندهای کلاینت + sing-box برای Hysteria2/TUIC.
- **gateway**: Xray مستقل که فقط اوتباند است؛ کانال‌ها با `leastPing` بالانس می‌شوند و اگر همه بخوابند `direct` می‌شود.
- کانفیگ‌ها از `config/inventory.json` رندر می‌شوند، پس لینک‌ها و UUIDها داخل گیت لو نمی‌روند.

---

## ۱) نصب

روی Ubuntu 22.04 یا 24.04 (root):

```bash
git clone https://github.com/m0000hamad/outlander.git
cd outlander
sudo bash scripts/install.sh
```

اسکریپت این کارها را انجام می‌دهد: نصب Docker، تنظیم BBR، ساخت `.env` با پسوردهای تصادفی، رندر کانفیگ، build و بالا آوردن کنسول و بقیهٔ سرویس‌ها، ست کردن یوزر/پس پنل، و ساخت یونیت systemd برای ماندگاری قوانین فایروال بعد از ریبوت.

در پایان آدرس کنسول، پنل و پسوردها چاپ می‌شود. بعد از آن:

```bash
make doctor     # چک سرور و سرویس‌ها
make ps
```

از همان اول می‌توانید اوتباند را هم بدهید:

```bash
sudo bash scripts/install.sh --outbound 'vless://<uuid>@abr02.example.ir:80?encryption=none&security=none&type=ws&host=link.example-cdn.ir&path=%2Flink%2F<token>#CDN2'
```

---

## ۲) کنسول وب

`http://<server-ip>:8099` — یوزرنیم `admin` و پسورد همان `CONSOLE_PASSWORD` در `.env` (نصب‌کننده می‌سازد).

| بخش | کار |
|---|---|
| **داشبورد** | وضعیت gateway / panel / sing-box، دکمهٔ ری‌استارت/استارت/استاپ، رندر و اعمال |
| **اوتباندها** | کادر متنی برای چسباندن چند لینک (هر خط یکی)، گروه، IP جایگزین DNS، زنجیره (`via`)، فعال/غیرفعال و حذف کانال |
| **فورواردینگ** | افزودن پورت/محدوده/همهٔ پورت‌های یک IP به مقصد دلخواه، تنظیم پورت‌هاپینگ Hysteria2، اعمال فوری روی فایروال |

هر تغییری که در کنسول بدهید: کانفیگ رندر می‌شود، گیت‌وی ری‌استارت می‌شود و قوانین nftables اعمال می‌شوند. دکمهٔ **«وصل کردن پنل به گیت‌وی»** هم همان کاری را می‌کند که `make panel` (پچ تمپلیت Xray پنل).

نمونهٔ فوروارد:

- `tcp` / پورت `3389` / مقصد `10.0.0.5:3389` → یک پورت به یک میزبان داخلی
- `both` / `50000-50100` / `172.28.60.12` → کل یک محدوده به یک کانتینر
- `tcp` / `*` / IP ورودی `203.0.113.5` / `10.0.0.9` → **همهٔ پورت‌های** یک IP به میزبان دیگر (بدون تغییر پورت)

> کنسول اختیار root روی سرور را دارد (فایروال و کانتینرها). پسورد قوی بگذارید، پورتش را در دسترس عموم رها نکنید و ترجیحاً از تونل SSH استفاده کنید: `ssh -L 8099:127.0.0.1:8099 root@server`.

جزئیات: [docs/CONSOLE.md](docs/CONSOLE.md)

---

## ۳) مدیریت اوتباندها (CLI، اختیاری)

```bash
python3 -m outlander add '<link>' --tag starlink-cdn2 --group cdn
python3 -m outlander list
python3 -m outlander rm starlink-cdn2
python3 -m outlander render && docker compose restart gateway
```

| گزینه | کاربرد |
|---|---|
| `--tag` | نام کوتاه کانال |
| `--group` | گروه؛ برای هر گروه یک بالانسر جدا ساخته می‌شود |
| `--resolve 1.2.3.4` | پین کردن IP دامنهٔ CDN وقتی DNS مسموم است |
| `--via <tag>` | زنجیره: اول از کانال دیگر رد شود |

اگر از کنسول استفاده کرده‌اید، فایل‌های کانفیگ با مالکیت کاربر نصب ساخته می‌شوند (`HOST_UID`)، وگرنه CLI را با `sudo` بزنید.

پروتکل‌های اوتباند: `vless`, `vmess`, `trojan`, `ss`, `socks`, `http`, `hysteria2`, `tuic`. مسیر WS، هدر `Host`، REALITY و TLS خودکار از لینک خوانده می‌شود.

---

## ۴) اینباندهای پنل

پنل روی `2053` بالا می‌آید (host network، تا IP واقعی کلاینت دیده شود).

| پروتکل | پورت | نکته |
|---|---|---|
| VLESS + REALITY | 443/tcp | `dest` یک سایت واقعی مثل `www.microsoft.com:443`، `flow`: `xtls-rprx-vision` |
| VLESS + WS | 8080/tcp | بدون TLS، پشت CDN |
| VLESS + WS + TLS | 8443/tcp | سرتیفیکیت خودتان، CDN حالت Full |
| Trojan | 2087/tcp | با سرتیفیکیت |
| Shadowsocks-2022 | 8388/tcp+udp | سبک و سریع |
| Hysteria2 | 8443/udp | sing-box، با obfs salamander و پورت‌هاپینگ |
| TUIC | 8444/udp | sing-box |

مرحله‌به‌مرحله: [docs/PANEL.md](docs/PANEL.md)

---

## ۵) CDN (مخفی کردن IP سرور)

۱. دامنه را در CDN اضافه کنید (Cloudflare / CloudyFa / ArvanCloud)، رکورد `A` به IP سرور با پروکسی روشن.
۲. پورت مبدأ `8080` (بدون TLS) یا `8443` (Full).
۳. WebSocket فعال؛ `Host` کلاینت = دامنهٔ CDN، `path` مثل `/ol`.
۴. برای اینکه IP سرور مستقیم جواب ندهد:

```bash
sudo bash scripts/cdn-firewall.sh                        # فقط رنج‌های Cloudflare به 80/8080/8443
sudo ADMIN_IPS='1.2.3.4' bash scripts/cdn-firewall.sh    # IP خودتان هم مجاز
```

REALITY را پشت CDN نگذارید؛ باید مستقیم روی IP باشد. جزئیات: [docs/CDN.md](docs/CDN.md)

---

## ۶) فورواردینگ

از کنسول، یا با CLI:

```bash
python3 -m outlander forward add --proto tcp  --listen 3389 --target 10.0.0.5:3389
python3 -m outlander forward add --proto both --listen 50000-50100 --target 172.28.60.12
python3 -m outlander forward add --proto tcp  --listen '*' --listen-ip 203.0.113.5 --target 10.0.0.9
python3 -m outlander forward list
python3 -m outlander forward render && sudo bash scripts/forward-apply.sh
```

- قوانین در `config/nftables/outlander.nft` رندر می‌شوند و با یونیت `outlander-forward.service` بعد از ریبوت هم برمی‌گردند.
- پورت‌هاپینگ Hysteria2: کلاینت `mport=20000-40000` می‌زند و همه به `172.28.60.12:8443` می‌رسند.
- روتر SNI (چند دامنه روی یک IP): `COMPOSE_PROFILES=extra,ha` و `config/haproxy/haproxy.cfg`.

جزئیات: [docs/FORWARDING.md](docs/FORWARDING.md)

---

## ۷) Hysteria2 و سرتیفیکیت

```bash
sudo bash scripts/cert.sh hy2.example.com
# .env: HY2_ENABLED=1 و HY2_SNI=hy2.example.com
python3 -m outlander render && docker compose --profile extra up -d
```

کلاینت: `hysteria2://<HY2_PASSWORD>@<ip>:8443?sni=hy2.example.com&obfs=salamander&obfs-password=<HY2_OBFS_PASSWORD>&mport=20000-40000`

---

## ۸) دستورهای روزمره

```bash
make up | down | restart | logs | ps
make test        # یونیت‌تست + xray -test روی کانفیگ رندرشده
make doctor
make backup      # .env + config + دیتابیس پنل + سرتیفیکیت‌ها
make panel       # وصل کردن پنل به گیت‌وی
make console-logs
```

آپدیت: `git pull && docker compose up -d --build` و بعد `make panel`.
[docs/OPERATIONS.md](docs/OPERATIONS.md)

---

## ساختار

```
outlander/
├── outlander/            # CLI و رندر کانفیگ (بدون وابستگی)
├── console/              # کنسول وب (Python stdlib + Dockerfile)
├── config/               # inventory، کانفیگ‌های رندرشده، haproxy، nftables
├── scripts/              # install, cert, firewall, backup, forward
├── tests/
└── docs/
```

`.env`، `inventory.json`، کانفیگ‌های رندرشده، `data/` و `certs/` در `.gitignore` هستند: لینک‌های اوتباند رمز محسوب می‌شوند.

---

## نکته

این ابزار برای دسترسی به اینترنت آزاد و مدیریت سرور خودتان است. مسئولیت رعایت قوانین محلی و شرایط سرویس ارائه‌دهنده (سرور و CDN) با شماست.
