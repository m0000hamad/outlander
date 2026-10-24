import base64
import json
import unittest

from outlander import render
from outlander.uri import LinkError, parse
from outlander.xray import xray_outbound

CDN = (
    "vless://21e57352-de4a-4e76-b3ed-ec38631d76a0@abr02.example.ir:80"
    "?encryption=none&security=none&type=ws&host=link.example-cdn.ir"
    "&path=%2Flink%2FdemoToken#CDN%20Server%202"
)


class VlessTests(unittest.TestCase):
    def test_cdn_websocket_link(self):
        link = parse(CDN)
        self.assertEqual(link.scheme, "vless")
        self.assertEqual(link.address, "abr02.example.ir")
        self.assertEqual(link.port, 80)
        self.assertEqual(link.uuid, "21e57352-de4a-4e76-b3ed-ec38631d76a0")
        self.assertEqual(link.network, "ws")
        self.assertEqual(link.security, "none")
        self.assertEqual(link.host, "link.example-cdn.ir")
        self.assertEqual(link.path, "/link/demoToken")
        self.assertEqual(link.name, "CDN Server 2")

    def test_cdn_link_becomes_xray_outbound(self):
        outbound = xray_outbound(parse(CDN), "ol-cdn-cdn2")
        self.assertEqual(outbound["protocol"], "vless")
        self.assertEqual(outbound["settings"]["vnext"][0]["address"], "abr02.example.ir")
        stream = outbound["streamSettings"]
        self.assertEqual(stream["network"], "ws")
        self.assertEqual(stream["security"], "none")
        self.assertEqual(stream["wsSettings"]["path"], "/link/demoToken")
        self.assertEqual(stream["wsSettings"]["headers"]["Host"], "link.example-cdn.ir")

    def test_reality_link(self):
        raw = (
            "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443"
            "?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome"
            "&pbk=PUBKEY&sid=abcd&type=tcp&flow=xtls-rprx-vision#r"
        )
        stream = xray_outbound(parse(raw), "reality")["streamSettings"]
        self.assertEqual(stream["security"], "reality")
        self.assertEqual(stream["realitySettings"]["publicKey"], "PUBKEY")
        self.assertEqual(stream["realitySettings"]["shortId"], "abcd")

    def test_missing_port_uses_default(self):
        link = parse("vless://11111111-2222-3333-4444-555555555555@example.com?type=ws")
        self.assertEqual(link.port, 443)


class OtherSchemeTests(unittest.TestCase):
    def test_trojan(self):
        link = parse("trojan://secret@example.com:8443?sni=example.com&type=tcp#t")
        self.assertEqual(link.password, "secret")
        self.assertEqual(link.security, "none")

    def test_shadowsocks_sip002(self):
        userinfo = base64.urlsafe_b64encode(b"aes-256-gcm:pass word").decode().rstrip("=")
        link = parse(f"ss://{userinfo}@example.com:8388#ss")
        self.assertEqual(link.method, "aes-256-gcm")
        self.assertEqual(link.password, "pass word")

    def test_shadowsocks_legacy(self):
        payload = base64.b64encode(b"chacha20-ietf-poly1305:pw@example.com:8388").decode()
        link = parse(f"ss://{payload}#legacy")
        self.assertEqual(link.address, "example.com")
        self.assertEqual(link.method, "chacha20-ietf-poly1305")

    def test_vmess_json(self):
        body = {
            "v": "2",
            "ps": "vm",
            "add": "example.com",
            "port": "443",
            "id": "11111111-2222-3333-4444-555555555555",
            "aid": "0",
            "net": "ws",
            "host": "cdn.example.com",
            "path": "/ws",
            "tls": "tls",
            "sni": "cdn.example.com",
        }
        raw = "vmess://" + base64.b64encode(json.dumps(body).encode()).decode()
        link = parse(raw)
        self.assertEqual(link.scheme, "vmess")
        self.assertEqual(link.port, 443)
        self.assertEqual(link.params["sni"], "cdn.example.com")
        self.assertTrue(link.security == "tls")

    def test_hysteria2(self):
        link = parse("hysteria2://pass@example.com:8443?sni=hy.example.com&obfs=salamander&obfs-password=x#hy")
        self.assertTrue(link.is_singbox)
        self.assertEqual(link.password, "pass")
        self.assertEqual(link.params["obfs"], "salamander")

    def test_tuic(self):
        link = parse("tuic://uuid-here:pw@example.com:8444?congestion_control=bbr#tuic")
        self.assertEqual(link.uuid, "uuid-here")
        self.assertEqual(link.password, "pw")

    def test_unknown_scheme(self):
        with self.assertRaises(LinkError):
            parse("wireguard://whatever")

    def test_garbage(self):
        with self.assertRaises(LinkError):
            parse("not a link")


class RenderTests(unittest.TestCase):
    def inventory(self):
        return {
            "version": 1,
            "secret": "s" * 32,
            "settings": {"direct_domestic": True, "block_ads": True, "strategy": "leastPing"},
            "channels": [
                {"tag": "cdn2", "group": "cdn", "enabled": True, "link": CDN},
                {"tag": "hy2a", "group": "hy2", "enabled": True, "link": "hysteria2://pw@example.com:8443?sni=hy.example.com"},
            ],
        }

    def test_gateway_has_balancer_and_chain(self):
        config = render.gateway_config(self.inventory(), {"GATEWAY_SOCKS_PORT": "10808"})
        tags = [outbound["tag"] for outbound in config["outbounds"]]
        self.assertIn("ol-cdn-cdn2", tags)
        self.assertIn("ol-hy2-hy2a", tags)
        self.assertEqual(tags[-2:], ["direct", "block"])
        self.assertEqual([b["tag"] for b in config["balancers"]], ["ol-bal-cdn", "ol-bal-hy2", "ol-auto"])
        last = config["routing"]["rules"][-1]
        self.assertEqual(last["balancerTag"], "ol-auto")
        self.assertIn("burstObservatory", config)

    def test_hysteria_channel_uses_socks_bridge(self):
        config = render.gateway_config(self.inventory(), {})
        hy2 = next(item for item in config["outbounds"] if item["tag"] == "ol-hy2-hy2a")
        self.assertEqual(hy2["protocol"], "socks")
        self.assertEqual(hy2["settings"]["servers"][0]["port"], render.SINGBOX_SOCKS_BASE)

    def test_singbox_mirrors_socks_inbound(self):
        config = render.singbox_config(self.inventory(), {})
        socks = config["inbounds"][0]
        self.assertEqual(socks["listen_port"], render.SINGBOX_SOCKS_BASE)
        self.assertEqual(config["outbounds"][0]["type"], "hysteria2")
        self.assertEqual(config["route"]["final"], "gw")

    def test_panel_fragments(self):
        outbound, rule = render.panel_fragments(self.inventory(), {})
        self.assertEqual(outbound["tag"], render.GATEWAY_TAG)
        self.assertEqual(rule["outboundTag"], render.GATEWAY_TAG)


if __name__ == "__main__":
    unittest.main()
