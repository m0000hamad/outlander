"""Server-rendered HTML for the Outlander console."""

from __future__ import annotations

import html

FONT = 'system-ui,"Segoe UI",Tahoma,"Iranian Sans",sans-serif'
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

STYLE = f"""
:root{{
  --bg:#0a0d13;--panel:#111722;--panel2:#0d131d;--line:#1f2837;--text:#e7edf6;
  --muted:#8b96a9;--accent:#5b8cff;--ok:#35c07e;--warn:#e0a13a;--bad:#ef5f5f;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1100px 520px at 85% -10%,#17263e 0,var(--bg) 58%);
  color:var(--text);font:14px/1.65 {FONT};min-height:100vh}}
a{{color:var(--accent);text-decoration:none}}
code,pre,.mono{{font-family:{MONO};direction:ltr;unicode-bidi:embed;text-align:left}}
.top{{display:flex;gap:14px;align-items:center;padding:12px 22px;border-bottom:1px solid var(--line);
  background:rgba(9,12,18,.82);backdrop-filter:blur(8px);position:sticky;top:0;z-index:5}}
.brand{{font-weight:700;letter-spacing:.4px}}
.brand em{{font-style:normal;color:var(--muted);font-weight:400;font-size:12px;margin-inline-start:6px}}
nav{{display:flex;gap:4px;margin-inline-start:auto;align-items:center}}
nav a{{padding:7px 12px;border-radius:9px;color:var(--muted);font-size:13px}}
nav a.on{{background:#1a2331;color:var(--text)}}
main{{max-width:1100px;margin:0 auto;padding:24px 20px 70px}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:var(--muted);margin:0 0 20px;font-size:13px}}
.grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}}
.card{{background:linear-gradient(180deg,#131a26,#101620);border:1px solid var(--line);
  border-radius:14px;padding:16px 16px 14px}}
.card h2{{margin:0 0 10px;font-size:14px;font-weight:600}}
.card p{{margin:0 0 12px;color:var(--muted);font-size:12.5px}}
.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}}
.field{{display:flex;flex-direction:column;gap:5px;flex:1 1 130px}}
.field label{{font-size:11.5px;color:var(--muted)}}
input,select,textarea{{background:var(--panel2);border:1px solid var(--line);color:var(--text);
  border-radius:9px;padding:9px 11px;font:inherit;font-size:13px;width:100%}}
input:focus,select:focus,textarea:focus{{outline:none;border-color:#33518a;box-shadow:0 0 0 3px #1b2b47}}
textarea{{min-height:104px;resize:vertical;font-family:{MONO};direction:ltr;text-align:left;font-size:12.5px}}
.btn{{display:inline-flex;align-items:center;gap:6px;border:1px solid #2f4f8f;background:#1d2f52;
  color:#dce7ff;padding:9px 14px;border-radius:9px;font:inherit;font-size:13px;cursor:pointer}}
.btn:hover{{background:#243a63}}
.btn.ghost{{background:transparent;border-color:var(--line);color:var(--muted)}}
.btn.danger{{border-color:#5d2a2a;background:#2a1616;color:#ffbdbd}}
.btn.ok{{border-color:#245c42;background:#123024;color:#b9f0d3}}
.btn.sm{{padding:6px 10px;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}}
th{{text-align:right;font-weight:500;color:var(--muted);font-size:11.5px;padding:8px 10px;border-bottom:1px solid var(--line)}}
td{{padding:10px;border-bottom:1px solid #18202d;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
.badge{{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;
  font-size:11.5px;border:1px solid var(--line);color:var(--muted)}}
.badge .dot{{width:7px;height:7px;border-radius:50%;background:var(--muted)}}
.badge.on{{color:#b9f0d3;border-color:#245c42;background:#0f2419}}
.badge.on .dot{{background:var(--ok)}}
.badge.off{{color:#ffc9c9;border-color:#5d2a2a;background:#241111}}
.badge.off .dot{{background:var(--bad)}}
.badge.warn{{color:#ffe0ae;border-color:#5a4520;background:#241c0d}}
.badge.warn .dot{{background:var(--warn)}}
.flash{{border-radius:11px;padding:11px 14px;margin:0 0 16px;font-size:13px;border:1px solid var(--line);
  background:#121a26;white-space:pre-wrap}}
.flash.ok{{border-color:#245c42;background:#0f2419;color:#c6f4dd}}
.flash.err{{border-color:#5d2a2a;background:#251313;color:#ffcccc}}
.muted{{color:var(--muted)}}
.tiny{{font-size:11.5px}}
.split{{display:grid;gap:14px;grid-template-columns:1.35fr 1fr}}
@media (max-width:860px){{.split{{grid-template-columns:1fr}}}}
.hr{{height:1px;background:var(--line);margin:14px 0}}
.login{{min-height:100vh;display:grid;place-items:center;padding:20px}}
.login .card{{width:100%;max-width:380px}}
footer{{color:var(--muted);font-size:11.5px;text-align:center;padding:0 20px 30px}}
"""


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _nav(active: str) -> str:
    items = [("/", "داشبورد"), ("/outbounds", "اوتباندها"), ("/forwarding", "فورواردینگ")]
    links = "".join(
        f'<a class="{"on" if path == active else ""}" href="{path}">{label}</a>' for path, label in items
    )
    return (
        '<header class="top">'
        '<div class="brand">Outlander <em>console</em></div>'
        f'<nav>{links}'
        '<form method="post" action="/logout" style="margin:0">'
        '<button class="btn ghost sm" type="submit">خروج</button></form></nav>'
        "</header>"
    )


def layout(title: str, body: str, active: str = "/", flash_html: str = "") -> str:
    return (
        "<!doctype html><html lang=\"fa\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} · Outlander</title><style>{STYLE}</style></head><body>"
        f"{_nav(active)}<main>{flash_html}{body}</main>"
        "<footer>Outlander console · تغییرات مستقیماً روی gateway، sing-box و فایروال اعمال می‌شود</footer>"
        "</body></html>"
    )


def flash_html(message: str = "", level: str = "ok") -> str:
    if not message:
        return ""
    css = "err" if level == "err" else "ok"
    return f'<div class="flash {css}">{esc(message)}</div>'


def badge(state: str) -> str:
    mapping = {
        "running": ("on", "running"),
        "exited": ("off", "stopped"),
        "missing": ("off", "missing"),
        "unknown": ("warn", "unknown"),
        "created": ("warn", "created"),
        "restarting": ("warn", "restarting"),
    }
    css, label = mapping.get(state, ("warn", state or "unknown"))
    return f'<span class="badge {css}"><span class="dot"></span>{esc(label)}</span>'


def csrf_input(token: str) -> str:
    return f'<input type="hidden" name="_csrf" value="{esc(token)}">'


def dashboard(ctx: dict) -> str:
    states = ctx["states"]
    cards = []
    for key, label, hint in (
        ("gateway", "Gateway", "زنجیره اوتباند و بالانسر"),
        ("panel", "Panel (3x-ui)", "اینباندها و کاربران"),
        ("singbox", "sing-box", "Hysteria2 / TUIC"),
    ):
        info = states.get(key, {"state": "unknown", "detail": ""})
        cards.append(
            '<div class="card">'
            f'<h2>{esc(label)}</h2>'
            f'<p>{esc(hint)}</p>'
            f'<div class="row" style="justify-content:space-between">'
            f'{badge(info.get("state", "unknown"))}'
            f'<span class="tiny muted">{esc(info.get("detail", ""))}</span></div>'
            '<div class="hr"></div>'
            '<form method="post" action="/service/action" class="row" style="margin:0">'
            f'{csrf_input(ctx["csrf"])}'
            f'<input type="hidden" name="service" value="{esc(key)}">'
            '<button class="btn ghost sm" name="action" value="restart">ری‌استارت</button>'
            '<button class="btn ghost sm" name="action" value="start">استارت</button>'
            '<button class="btn ghost sm" name="action" value="stop">استاپ</button>'
            "</form></div>"
        )

    enabled = sum(1 for channel in ctx["channels"] if channel["enabled"])
    hop = ctx["hop"]
    hop_label = "روشن" if hop.get("enabled") else "خاموش"
    last = ctx["state"].get("apply") or ctx["state"].get("panel") or {}

    summary = (
        '<div class="card"><h2>وضعیت</h2>'
        f'<p>کانال‌های فعال: <b>{enabled}</b> از {len(ctx["channels"])} · قوانین فوروارد: '
        f'<b>{len(ctx["rules"])}</b> · پورت‌هاپینگ: <b>{esc(hop_label)}</b></p>'
        f'<p class="tiny">آخرین عملیات: {esc(last.get("at", "-"))} — {esc(last.get("message", ""))}</p>'
        '<div class="row">'
        '<form method="post" action="/outbounds/apply" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn" type="submit">رندر و ری‌استارت گیت‌وی</button></form>'
        '<form method="post" action="/forwarding/apply" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn ok" type="submit">اعمال فورواردینگ</button></form>'
        '<form method="post" action="/panel/route" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn ghost" type="submit">وصل کردن پنل به گیت‌وی</button></form>'
        "</div></div>"
    )

    body = (
        "<h1>داشبورد</h1>"
        '<p class="sub">هر تغییری که اینجا می‌دهید در کانفیگ رندر می‌شود؛ دکمه‌های بالا برای اعمال دستی است.</p>'
        f'<div class="grid">{"".join(cards)}{summary}</div>'
        '<div class="card" style="margin-top:14px"><h2>شروع سریع</h2>'
        '<p>۱) در تب <a href="/outbounds">اوتباندها</a> لینک‌ها را (هر خط یکی) بچسبانید. '
        '۲) در تب <a href="/forwarding">فورواردینگ</a> پورت/آی‌پی مقصد را اضافه کنید. '
        '۳) اینباندهای کلاینت را در پنل بسازید (<code>docs/PANEL.md</code>).</p>'
        f'<p class="tiny">گیت‌وی روی <code>127.0.0.1:{esc(ctx["socks_port"])}</code> گوش می‌دهد و پنل از همان استفاده می‌کند.</p></div>'
    )
    return layout("داشبورد", body, "/", ctx["flash"])


def outbounds(ctx: dict) -> str:
    via_options = "".join(f'<option value="{esc(c["tag"])}">{esc(c["tag"])}</option>' for c in ctx["channels"])
    add_form = (
        '<div class="card"><h2>افزودن اوتباند</h2>'
        '<p>هر خط یک لینک: <code>vless://</code>، <code>vmess://</code>، <code>trojan://</code>، '
        '<code>ss://</code>، <code>hysteria2://</code>، <code>tuic://</code></p>'
        '<form method="post" action="/outbounds/add">'
        f'{csrf_input(ctx["csrf"])}'
        '<div class="field" style="margin-bottom:10px"><label>لینک‌ها</label>'
        '<textarea name="links" placeholder="vless://uuid@host:80?type=ws&amp;security=none&amp;host=cdn.example.com&amp;path=%2Flink%2Ftoken#cdn2" required></textarea></div>'
        '<div class="row" style="margin-bottom:10px">'
        '<div class="field"><label>نام (اختیاری، فقط برای یک لینک)</label><input name="tag" placeholder="cdn2"></div>'
        '<div class="field"><label>گروه</label><input name="group" value="cdn"></div>'
        '<div class="field"><label>IP جایگزین DNS (اختیاری)</label><input name="resolve" placeholder="104.16.1.2" class="mono"></div>'
        '<div class="field"><label>از طریق (زنجیره)</label><select name="via"><option value="">—</option>'
        f'{via_options}</select></div></div>'
        '<button class="btn" type="submit">افزودن و اعمال</button></form></div>'
    )

    if ctx["channels"]:
        rows = []
        for channel in ctx["channels"]:
            state = (
                '<span class="badge on"><span class="dot"></span>فعال</span>'
                if channel["enabled"]
                else '<span class="badge off"><span class="dot"></span>غیرفعال</span>'
            )
            rows.append(
                "<tr>"
                f'<td><b>{esc(channel["tag"])}</b><div class="tiny muted">{esc(channel["group"])}</div></td>'
                f'<td>{esc(channel["scheme"])}</td>'
                f'<td class="mono tiny">{esc(channel["endpoint"])}</td>'
                f'<td class="mono tiny">{esc(channel["detail"])}</td>'
                f'<td>{state}</td>'
                '<td><div class="row" style="gap:6px;flex-wrap:nowrap">'
                '<form method="post" action="/outbounds/toggle" style="margin:0">'
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="tag" value="{esc(channel["tag"])}">'
                '<button class="btn ghost sm" type="submit">تغییر وضعیت</button></form>'
                '<form method="post" action="/outbounds/delete" style="margin:0" '
                'onsubmit="return confirm(\'این کانال حذف شود؟\')">'
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="tag" value="{esc(channel["tag"])}">'
                '<button class="btn danger sm" type="submit">حذف</button></form>'
                "</div></td></tr>"
            )
        table = (
            '<div class="card" style="margin-top:14px"><h2>کانال‌ها</h2>'
            "<table><thead><tr><th>نام</th><th>پروتکل</th><th>مقصد</th><th>مسیر / Host</th>"
            "<th>وضعیت</th><th></th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            '<p class="tiny muted" style="margin-top:10px">با افزودن/حذف، کانفیگ خودکار رندر و گیت‌وی ری‌استارت می‌شود.'
            " ترافیک بین کانال‌ها با <code>leastPing</code> تقسیم و در صورت قطعی به بعدی منتقل می‌شود.</p></div>"
        )
    else:
        table = (
            '<div class="card" style="margin-top:14px"><h2>کانال‌ها</h2>'
            '<p class="muted">هنوز هیچ اوتباندی ثبت نشده است. در این حالت گیت‌وی همه‌چیز را مستقیم رد می‌کند.</p></div>'
        )

    body = (
        "<h1>اوتباندها</h1>"
        '<p class="sub">لینک‌هایی که ترافیک از آن‌ها به اینترنت بین‌الملل می‌رسد. مسیر WS، هدر Host، REALITY و TLS '
        "به‌صورت خودکار از لینک خوانده می‌شود.</p>"
        f'{add_form}{table}'
    )
    return layout("اوتباندها", body, "/outbounds", ctx["flash"])


def forwarding(ctx: dict) -> str:
    hop = ctx["hop"]
    hop_card = (
        '<div class="card"><h2>پورت‌هاپینگ Hysteria2</h2>'
        '<p>کلاینت روی یک پورت تصادفی از این محدوده وصل می‌شود و سرور همه را به پورت اصلی sing-box می‌فرستد.</p>'
        '<form method="post" action="/forwarding/hop">'
        f'{csrf_input(ctx["csrf"])}'
        '<div class="row" style="margin-bottom:10px">'
        '<div class="field"><label>فعال</label>'
        f'<select name="enabled"><option value="1" {"selected" if hop.get("enabled") else ""}>بله</option>'
        f'<option value="0" {"" if hop.get("enabled") else "selected"}>خیر</option></select></div>'
        '<div class="field"><label>پروتکل</label>'
        f'<select name="proto"><option value="udp" {"selected" if hop.get("proto") == "udp" else ""}>udp</option>'
        f'<option value="tcp" {"selected" if hop.get("proto") == "tcp" else ""}>tcp</option></select></div>'
        '<div class="field"><label>محدوده پورت</label>'
        f'<input name="range" value="{esc(hop.get("range", "20000-40000"))}" class="mono"></div>'
        '<div class="field"><label>مقصد</label>'
        f'<input name="target" value="{esc(hop.get("target", "172.28.60.12:8443"))}" class="mono"></div></div>'
        '<button class="btn" type="submit">ذخیره</button></form></div>'
    )

    add_card = (
        '<div class="card"><h2>افزودن فوروارد</h2>'
        '<p>پورت (یا محدوده، یا <code>*</code> برای همه پورت‌های یک IP) را به یک مقصد داخلی/خارجی بفرستید.</p>'
        '<form method="post" action="/forwarding/add">'
        f'{csrf_input(ctx["csrf"])}'
        '<div class="row" style="margin-bottom:10px">'
        '<div class="field"><label>پروتکل</label><select name="proto">'
        '<option value="tcp">tcp</option><option value="udp">udp</option><option value="both">both</option></select></div>'
        '<div class="field"><label>IP ورودی (اختیاری)</label><input name="listen_ip" placeholder="203.0.113.5" class="mono"></div>'
        '<div class="field"><label>پورت ورودی</label><input name="listen" placeholder="443 یا 50000-50100 یا *" class="mono" required></div>'
        '<div class="field"><label>مقصد</label><input name="target" placeholder="10.0.0.5:443" class="mono" required></div></div>'
        '<button class="btn" type="submit">افزودن و اعمال</button></form></div>'
    )

    if ctx["rules"]:
        rows = []
        for index, rule in enumerate(ctx["rules"]):
            rows.append(
                "<tr>"
                f'<td>{esc(rule.get("proto", "tcp"))}</td>'
                f'<td class="mono tiny">{esc(rule.get("listen_ip") or "همه")}</td>'
                f'<td class="mono tiny">{esc(rule.get("listen"))}</td>'
                f'<td class="mono tiny">{esc(rule.get("target"))}</td>'
                '<td><form method="post" action="/forwarding/delete" style="margin:0" '
                'onsubmit="return confirm(\'حذف شود؟\')">'
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="index" value="{index}">'
                '<button class="btn danger sm" type="submit">حذف</button></form></td></tr>'
            )
        table = (
            '<div class="card" style="margin-top:14px"><h2>قوانین فعال</h2>'
            "<table><thead><tr><th>پروتکل</th><th>IP ورودی</th><th>پورت</th><th>مقصد</th><th></th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table>'
            '<div class="row" style="margin-top:12px">'
            '<form method="post" action="/forwarding/apply" style="margin:0">'
            f'{csrf_input(ctx["csrf"])}<button class="btn ok" type="submit">اعمال روی فایروال</button></form>'
            '<span class="tiny muted">قوانین پس از هر ویرایش خودکار روی سرور اعمال می‌شوند؛ این دکمه برای اجرای دستی است.</span>'
            "</div></div>"
        )
    else:
        table = (
            '<div class="card" style="margin-top:14px"><h2>قوانین فعال</h2>'
            '<p class="muted">هیچ قانون فورواردی وجود ندارد.</p></div>'
        )

    body = (
        "<h1>فورواردینگ</h1>"
        '<p class="sub">انتقال پورت/IP با nftables؛ بدون واسط اضافی و با کمترین تأخیر.</p>'
        f'{add_card}<div style="height:14px"></div>{hop_card}{table}'
    )
    return layout("فورواردینگ", body, "/forwarding", ctx["flash"])


def login(error: str = "") -> str:
    flash_markup = f'<div class="flash err">{esc(error)}</div>' if error else ""
    return (
        '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>ورود · Outlander</title><style>{STYLE}</style></head>"
        '<body><div class="login"><div class="card">'
        '<h2 style="font-size:17px;margin-bottom:2px">Outlander console</h2>'
        '<p class="muted tiny">برای مدیریت اوتباندها و فورواردینگ وارد شوید</p>'
        f"{flash_markup}"
        '<form method="post" action="/login">'
        '<div class="field" style="margin-bottom:10px"><label>کاربر</label>'
        '<input name="user" autocomplete="username" required autofocus></div>'
        '<div class="field" style="margin-bottom:14px"><label>رمز</label>'
        '<input name="password" type="password" autocomplete="current-password" required></div>'
        '<button class="btn" style="width:100%;justify-content:center" type="submit">ورود</button>'
        "</form></div></div></body></html>"
    )
