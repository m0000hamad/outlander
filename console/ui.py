"""Server-rendered HTML for the Outlander console (Persian / English)."""

from __future__ import annotations

import html

from .i18n import direction, normalise, t

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
  background:rgba(9,12,18,.82);backdrop-filter:blur(8px);position:sticky;top:0;z-index:5;flex-wrap:wrap}}
.brand{{font-weight:700;letter-spacing:.4px}}
.brand em{{font-style:normal;color:var(--muted);font-weight:400;font-size:12px;margin-inline-start:6px}}
nav{{display:flex;gap:4px;margin-inline-start:auto;align-items:center;flex-wrap:wrap}}
nav a{{padding:7px 12px;border-radius:9px;color:var(--muted);font-size:13px}}
nav a.on{{background:#1a2331;color:var(--text)}}
.lang{{display:flex;gap:2px;border:1px solid var(--line);border-radius:9px;padding:2px;margin-inline-start:4px}}
.lang a{{padding:4px 9px;border-radius:7px;font-size:12px;line-height:1.2}}
.lang a.on{{background:#22314a;color:var(--text)}}
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
th{{text-align:start;font-weight:500;color:var(--muted);font-size:11.5px;padding:8px 10px;border-bottom:1px solid var(--line)}}
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
.hr{{height:1px;background:var(--line);margin:14px 0}}
.login{{min-height:100vh;display:grid;place-items:center;padding:20px}}
.login .card{{width:100%;max-width:380px}}
footer{{color:var(--muted);font-size:11.5px;text-align:center;padding:0 20px 30px}}
"""


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _nav(active: str, lang: str) -> str:
    items = [
        ("/", t(lang, "nav.dashboard")),
        ("/outbounds", t(lang, "nav.outbounds")),
        ("/forwarding", t(lang, "nav.forwarding")),
    ]
    links = "".join(
        f'<a class="{"on" if path == active else ""}" href="{path}">{esc(label)}</a>' for path, label in items
    )
    language = (
        '<span class="lang">'
        f'<a class="{"on" if normalise(lang) == "fa" else ""}" href="/lang/fa">FA</a>'
        f'<a class="{"on" if normalise(lang) == "en" else ""}" href="/lang/en">EN</a>'
        "</span>"
    )
    return (
        '<header class="top">'
        '<div class="brand">Outlander <em>console</em></div>'
        f"<nav>{links}{language}"
        '<form method="post" action="/logout" style="margin:0">'
        f'<button class="btn ghost sm" type="submit">{esc(t(lang, "nav.logout"))}</button></form></nav>'
        "</header>"
    )


def layout(title: str, body: str, active: str = "/", flash_html: str = "", lang: str = "fa") -> str:
    lang = normalise(lang)
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} · Outlander</title><style>{STYLE}</style></head><body>"
        f"{_nav(active, lang)}<main>{flash_html}{body}</main>"
        f"<footer>{esc(t(lang, 'footer'))}</footer>"
        "</body></html>"
    )


def flash_html(message: str = "", level: str = "ok") -> str:
    if not message:
        return ""
    css = "err" if level == "err" else "ok"
    return f'<div class="flash {css}">{esc(message)}</div>'


def badge(state: str, lang: str = "fa") -> str:
    mapping = {
        "running": "on",
        "exited": "off",
        "missing": "off",
        "created": "warn",
        "restarting": "warn",
        "starting": "warn",
        "unknown": "warn",
    }
    css = mapping.get(state, "warn")
    label = t(lang, f"state.{state}") if state in mapping else (state or t(lang, "state.unknown"))
    return f'<span class="badge {css}"><span class="dot"></span>{esc(label)}</span>'


def csrf_input(token: str) -> str:
    return f'<input type="hidden" name="_csrf" value="{esc(token)}">'


def dashboard(ctx: dict) -> str:
    lang = normalise(ctx.get("lang", "fa"))
    states = ctx["states"]
    cards = []
    for key, label_key, hint_key in (
        ("gateway", "dash.gateway", "dash.gateway_hint"),
        ("panel", "dash.panel", "dash.panel_hint"),
        ("singbox", "dash.singbox", "dash.singbox_hint"),
    ):
        info = states.get(key, {"state": "unknown", "detail": ""})
        cards.append(
            '<div class="card">'
            f'<h2>{esc(t(lang, label_key))}</h2>'
            f'<p>{esc(t(lang, hint_key))}</p>'
            '<div class="row" style="justify-content:space-between">'
            f'{badge(info.get("state", "unknown"), lang)}'
            f'<span class="tiny muted">{esc(info.get("detail", ""))}</span></div>'
            '<div class="hr"></div>'
            '<form method="post" action="/service/action" class="row" style="margin:0">'
            f'{csrf_input(ctx["csrf"])}'
            f'<input type="hidden" name="service" value="{esc(key)}">'
            f'<button class="btn ghost sm" name="action" value="restart">{esc(t(lang, "dash.restart"))}</button>'
            f'<button class="btn ghost sm" name="action" value="start">{esc(t(lang, "dash.start"))}</button>'
            f'<button class="btn ghost sm" name="action" value="stop">{esc(t(lang, "dash.stop"))}</button>'
            "</form></div>"
        )

    enabled = sum(1 for channel in ctx["channels"] if channel["enabled"])
    hop = ctx["hop"]
    hop_label = t(lang, "common.on" if hop.get("enabled") else "common.off")
    last = ctx["state"].get("apply") or ctx["state"].get("panel") or {}

    summary = (
        f'<div class="card"><h2>{esc(t(lang, "dash.status_title"))}</h2>'
        f'<p>{esc(t(lang, "dash.active_channels"))}: <b>{enabled}</b> {esc(t(lang, "dash.of"))} '
        f'{len(ctx["channels"])} · {esc(t(lang, "dash.forward_rules"))}: <b>{len(ctx["rules"])}</b> · '
        f'{esc(t(lang, "dash.hopping"))}: <b>{esc(hop_label)}</b></p>'
        f'<p class="tiny">{esc(t(lang, "dash.last_action"))}: {esc(last.get("at", "-"))} — '
        f'{esc(last.get("message", ""))}</p>'
        '<div class="row">'
        '<form method="post" action="/outbounds/apply" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn" type="submit">{esc(t(lang, "dash.render_restart"))}</button></form>'
        '<form method="post" action="/forwarding/apply" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn ok" type="submit">{esc(t(lang, "dash.apply_fwd"))}</button></form>'
        '<form method="post" action="/panel/route" style="margin:0">'
        f'{csrf_input(ctx["csrf"])}<button class="btn ghost" type="submit">{esc(t(lang, "dash.route_panel"))}</button></form>'
        "</div></div>"
    )

    body = (
        f"<h1>{esc(t(lang, 'dash.title'))}</h1>"
        f'<p class="sub">{esc(t(lang, "dash.subtitle"))}</p>'
        f'<div class="grid">{"".join(cards)}{summary}</div>'
        f'<div class="card" style="margin-top:14px"><h2>{esc(t(lang, "dash.quick_title"))}</h2>'
        f"<p>1) <a href=\"/outbounds\">{esc(t(lang, 'dash.quick_step1'))}</a> "
        f"2) <a href=\"/forwarding\">{esc(t(lang, 'dash.quick_step2'))}</a> "
        f"3) {esc(t(lang, 'dash.quick_step3'))}</p>"
        f'<p class="tiny">{esc(t(lang, "dash.gateway_listen", address="127.0.0.1:" + str(ctx["socks_port"])))}</p></div>'
    )
    return layout(t(lang, "dash.title"), body, "/", ctx["flash"], lang)


def outbounds(ctx: dict) -> str:
    lang = normalise(ctx.get("lang", "fa"))
    via_options = "".join(f'<option value="{esc(c["tag"])}">{esc(c["tag"])}</option>' for c in ctx["channels"])
    add_form = (
        f'<div class="card"><h2>{esc(t(lang, "ob.add_title"))}</h2>'
        f'<p>{esc(t(lang, "ob.hint"))}</p>'
        '<form method="post" action="/outbounds/add">'
        f'{csrf_input(ctx["csrf"])}'
        f'<div class="field" style="margin-bottom:10px"><label>{esc(t(lang, "ob.links"))}</label>'
        '<textarea name="links" placeholder="vless://uuid@host:80?type=ws&amp;security=none&amp;host=cdn.example.com&amp;path=%2Flink%2Ftoken#cdn2" required></textarea></div>'
        '<div class="row" style="margin-bottom:10px">'
        f'<div class="field"><label>{esc(t(lang, "ob.name_hint"))}</label><input name="tag" placeholder="cdn2"></div>'
        f'<div class="field"><label>{esc(t(lang, "common.group"))}</label><input name="group" value="cdn"></div>'
        f'<div class="field"><label>{esc(t(lang, "ob.resolve"))}</label><input name="resolve" placeholder="104.16.1.2" class="mono"></div>'
        f'<div class="field"><label>{esc(t(lang, "common.via"))}</label><select name="via"><option value="">—</option>'
        f'{via_options}</select></div></div>'
        f'<button class="btn" type="submit">{esc(t(lang, "ob.add_apply"))}</button></form></div>'
    )

    if ctx["channels"]:
        rows = []
        for channel in ctx["channels"]:
            state = (
                f'<span class="badge on"><span class="dot"></span>{esc(t(lang, "common.enabled"))}</span>'
                if channel["enabled"]
                else f'<span class="badge off"><span class="dot"></span>{esc(t(lang, "common.disabled"))}</span>'
            )
            rows.append(
                "<tr>"
                f'<td><b>{esc(channel["tag"])}</b><div class="tiny muted">{esc(channel["group"])}</div></td>'
                f'<td>{esc(channel["scheme"])}</td>'
                f'<td class="mono tiny">{esc(channel["endpoint"])}</td>'
                f'<td class="mono tiny">{esc(channel["detail"])}</td>'
                f"<td>{state}</td>"
                '<td><div class="row" style="gap:6px;flex-wrap:nowrap">'
                '<form method="post" action="/outbounds/toggle" style="margin:0">'
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="tag" value="{esc(channel["tag"])}">'
                f'<button class="btn ghost sm" type="submit">{esc(t(lang, "ob.toggle"))}</button></form>'
                '<form method="post" action="/outbounds/delete" style="margin:0" '
                f"onsubmit=\"return confirm('{esc(t(lang, 'ob.delete_confirm'))}')\">"
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="tag" value="{esc(channel["tag"])}">'
                f'<button class="btn danger sm" type="submit">{esc(t(lang, "common.delete"))}</button></form>'
                "</div></td></tr>"
            )
        table = (
            f'<div class="card" style="margin-top:14px"><h2>{esc(t(lang, "ob.table_title"))}</h2>'
            f"<table><thead><tr><th>{esc(t(lang, 'ob.col_name'))}</th><th>{esc(t(lang, 'ob.col_proto'))}</th>"
            f"<th>{esc(t(lang, 'ob.col_endpoint'))}</th><th>{esc(t(lang, 'ob.col_path'))}</th>"
            f"<th>{esc(t(lang, 'ob.col_state'))}</th><th></th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            f'<p class="tiny muted" style="margin-top:10px">{esc(t(lang, "ob.note"))}</p></div>'
        )
    else:
        table = (
            f'<div class="card" style="margin-top:14px"><h2>{esc(t(lang, "ob.table_title"))}</h2>'
            f'<p class="muted">{esc(t(lang, "ob.empty"))}</p></div>'
        )

    body = (
        f"<h1>{esc(t(lang, 'ob.title'))}</h1>"
        f'<p class="sub">{esc(t(lang, "ob.subtitle"))}</p>'
        f"{add_form}{table}"
    )
    return layout(t(lang, "ob.title"), body, "/outbounds", ctx["flash"], lang)


def forwarding(ctx: dict) -> str:
    lang = normalise(ctx.get("lang", "fa"))
    hop = ctx["hop"]
    hop_card = (
        f'<div class="card"><h2>{esc(t(lang, "hop.title"))}</h2>'
        f'<p>{esc(t(lang, "hop.hint"))}</p>'
        '<form method="post" action="/forwarding/hop">'
        f'{csrf_input(ctx["csrf"])}'
        '<div class="row" style="margin-bottom:10px">'
        f'<div class="field"><label>{esc(t(lang, "hop.enabled"))}</label>'
        f'<select name="enabled"><option value="1" {"selected" if hop.get("enabled") else ""}>{esc(t(lang, "common.yes"))}</option>'
        f'<option value="0" {"" if hop.get("enabled") else "selected"}>{esc(t(lang, "common.no"))}</option></select></div>'
        f'<div class="field"><label>{esc(t(lang, "common.protocol"))}</label>'
        f'<select name="proto"><option value="udp" {"selected" if hop.get("proto") == "udp" else ""}>udp</option>'
        f'<option value="tcp" {"selected" if hop.get("proto") == "tcp" else ""}>tcp</option></select></div>'
        f'<div class="field"><label>{esc(t(lang, "hop.range"))}</label>'
        f'<input name="range" value="{esc(hop.get("range", "20000-40000"))}" class="mono"></div>'
        f'<div class="field"><label>{esc(t(lang, "common.target"))}</label>'
        f'<input name="target" value="{esc(hop.get("target", "172.28.60.12:8443"))}" class="mono"></div></div>'
        f'<button class="btn" type="submit">{esc(t(lang, "common.save"))}</button></form></div>'
    )

    proxy_rows = []
    for item in ctx.get("proxy_ports", []):
        proxy_rows.append(
            "<tr>"
            f'<td><b>{esc(item["remark"])}</b></td>'
            f'<td class="mono tiny">{esc(item["port"])}</td>'
            f'<td class="tiny">{esc(item["security"])}</td>'
            f'<td><input class="mono tiny" readonly value="{esc(item["link"])}" onclick="this.select()"></td>'
            '<td><form method="post" action="/proxy/delete" style="margin:0" '
            f"onsubmit=\"return confirm('{esc(t(lang, 'proxy.delete_confirm'))}')\">"
            f'{csrf_input(ctx["csrf"])}<input type="hidden" name="id" value="{esc(item["id"])}">'
            f'<button class="btn danger sm" type="submit">{esc(t(lang, "common.delete"))}</button></form></td></tr>'
        )
    proxy_table = (
        f"<table><thead><tr><th>{esc(t(lang, 'common.name'))}</th><th>{esc(t(lang, 'proxy.port'))}</th>"
        f"<th>{esc(t(lang, 'proxy.kind'))}</th><th>{esc(t(lang, 'proxy.link'))}</th><th></th></tr></thead>"
        f'<tbody>{"".join(proxy_rows)}</tbody></table>'
        if proxy_rows
        else f'<p class="muted">{esc(t(lang, "proxy.empty"))}</p>'
    )
    proxy_card = (
        f'<div class="card"><h2>{esc(t(lang, "proxy.title"))}</h2>'
        f'<p>{esc(t(lang, "proxy.hint"))}</p>'
        + (f'<div class="flash err">{esc(ctx["proxy_error"])}</div>' if ctx.get("proxy_error") else "")
        + '<form method="post" action="/proxy/add">'
        + csrf_input(ctx["csrf"])
        + '<div class="row" style="margin-bottom:10px">'
        + f'<div class="field"><label>{esc(t(lang, "proxy.port"))}</label>'
        + '<input name="port" placeholder="2087" class="mono" required></div>'
        + f'<div class="field"><label>{esc(t(lang, "proxy.remark"))}</label>'
        + '<input name="remark" placeholder="proxy-1"></div>'
        + f'<div class="field"><label>{esc(t(lang, "proxy.kind"))}</label><select name="kind">'
        + f'<option value="reality">{esc(t(lang, "proxy.kind_reality"))}</option>'
        + f'<option value="ws">{esc(t(lang, "proxy.kind_ws"))}</option></select></div></div>'
        + f'<button class="btn" type="submit">{esc(t(lang, "proxy.add"))}</button></form>'
        + '<div class="hr"></div>'
        + f'<h2>{esc(t(lang, "proxy.table"))}</h2>'
        + proxy_table
        + "</div>"
    )

    add_card = (
        f'<div class="card"><h2>{esc(t(lang, "fw.add_title"))}</h2>'
        f'<p>{esc(t(lang, "fw.hint"))}</p>'
        '<form method="post" action="/forwarding/add">'
        f'{csrf_input(ctx["csrf"])}'
        '<div class="row" style="margin-bottom:10px">'
        f'<div class="field"><label>{esc(t(lang, "common.protocol"))}</label><select name="proto">'
        '<option value="tcp">tcp</option><option value="udp">udp</option><option value="both">both</option></select></div>'
        f'<div class="field"><label>{esc(t(lang, "common.listen_ip"))}</label><input name="listen_ip" placeholder="203.0.113.5" class="mono"></div>'
        f'<div class="field"><label>{esc(t(lang, "fw.listen_port"))}</label><input name="listen" placeholder="443 / 50000-50100 / *" class="mono" required></div>'
        f'<div class="field"><label>{esc(t(lang, "common.target"))}</label><input name="target" placeholder="10.0.0.5:443" class="mono" required></div></div>'
        f'<button class="btn" type="submit">{esc(t(lang, "fw.add_apply"))}</button></form></div>'
    )

    if ctx["rules"]:
        rows = []
        for index, rule in enumerate(ctx["rules"]):
            rows.append(
                "<tr>"
                f'<td>{esc(rule.get("proto", "tcp"))}</td>'
                f'<td class="mono tiny">{esc(rule.get("listen_ip") or t(lang, "common.all"))}</td>'
                f'<td class="mono tiny">{esc(rule.get("listen"))}</td>'
                f'<td class="mono tiny">{esc(rule.get("target"))}</td>'
                '<td><form method="post" action="/forwarding/delete" style="margin:0" '
                f"onsubmit=\"return confirm('{esc(t(lang, 'fw.delete_confirm'))}')\">"
                f'{csrf_input(ctx["csrf"])}<input type="hidden" name="index" value="{index}">'
                f'<button class="btn danger sm" type="submit">{esc(t(lang, "common.delete"))}</button></form></td></tr>'
            )
        table = (
            f'<div class="card" style="margin-top:14px"><h2>{esc(t(lang, "fw.table_title"))}</h2>'
            f"<table><thead><tr><th>{esc(t(lang, 'common.protocol'))}</th><th>{esc(t(lang, 'fw.col_ip'))}</th>"
            f"<th>{esc(t(lang, 'fw.col_port'))}</th><th>{esc(t(lang, 'fw.col_target'))}</th><th></th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table>'
            '<div class="row" style="margin-top:12px">'
            '<form method="post" action="/forwarding/apply" style="margin:0">'
            f'{csrf_input(ctx["csrf"])}<button class="btn ok" type="submit">{esc(t(lang, "fw.apply"))}</button></form>'
            f'<span class="tiny muted">{esc(t(lang, "fw.apply_note"))}</span>'
            "</div></div>"
        )
    else:
        table = (
            f'<div class="card" style="margin-top:14px"><h2>{esc(t(lang, "fw.table_title"))}</h2>'
            f'<p class="muted">{esc(t(lang, "fw.empty"))}</p></div>'
        )

    body = (
        f"<h1>{esc(t(lang, 'fw.title'))}</h1>"
        f'<p class="sub">{esc(t(lang, "fw.subtitle"))}</p>'
        f'{proxy_card}<div style="height:14px"></div>{add_card}<div style="height:14px"></div>{hop_card}{table}'
    )
    return layout(t(lang, "fw.title"), body, "/forwarding", ctx["flash"], lang)


def login(error: str = "", lang: str = "fa") -> str:
    lang = normalise(lang)
    flash_markup = f'<div class="flash err">{esc(error)}</div>' if error else ""
    return (
        f'<!doctype html><html lang="{lang}" dir="{direction(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(t(lang, 'login.submit'))} · Outlander</title><style>{STYLE}</style></head>"
        '<body><div class="login"><div class="card">'
        f'<h2 style="font-size:17px;margin-bottom:2px">{esc(t(lang, "login.title"))}</h2>'
        f'<p class="muted tiny">{esc(t(lang, "login.subtitle"))}</p>'
        f"{flash_markup}"
        '<form method="post" action="/login">'
        f'<div class="field" style="margin-bottom:10px"><label>{esc(t(lang, "login.user"))}</label>'
        '<input name="user" autocomplete="username" required autofocus></div>'
        f'<div class="field" style="margin-bottom:14px"><label>{esc(t(lang, "login.password"))}</label>'
        '<input name="password" type="password" autocomplete="current-password" required></div>'
        f'<button class="btn" style="width:100%;justify-content:center" type="submit">{esc(t(lang, "login.submit"))}</button>'
        "</form>"
        '<div class="lang" style="margin-top:14px;justify-content:center">'
        '<a class="on" href="/lang/fa">FA</a><a href="/lang/en">EN</a>'
        "</div></div></div></body></html>"
    )
