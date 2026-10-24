import unittest

from outlander import inbounds


def reality():
    return inbounds.reality_inbound(2053, "p1", "uuid-1", "priv", "pub", "abcd1234")


class RealityInboundTests(unittest.TestCase):
    def test_payload_shape(self):
        payload = reality()
        self.assertEqual(payload["port"], 2053)
        self.assertEqual(payload["protocol"], "vless")
        self.assertEqual(payload["streamSettings"]["network"], "tcp")
        self.assertEqual(payload["streamSettings"]["security"], "reality")
        reality_settings = payload["streamSettings"]["realitySettings"]
        self.assertEqual(reality_settings["dest"], "www.microsoft.com:443")
        self.assertEqual(reality_settings["serverNames"], ["www.microsoft.com"])
        self.assertEqual(reality_settings["shortIds"], ["abcd1234"])
        self.assertEqual(reality_settings["privateKey"], "priv")
        client = payload["settings"]["clients"][0]
        self.assertEqual(client["id"], "uuid-1")
        self.assertEqual(client["flow"], "xtls-rprx-vision")

    def test_link_roundtrip(self):
        link = inbounds.link_for(reality(), "1.2.3.4")
        self.assertIn("vless://uuid-1@1.2.3.4:2053", link)
        self.assertIn("security=reality", link)
        self.assertIn("pbk=pub", link)
        self.assertIn("sid=abcd1234", link)
        self.assertIn("sni=www.microsoft.com", link)
        self.assertIn("flow=xtls-rprx-vision", link)

    def test_missing_public_key_has_no_link(self):
        payload = reality()
        payload["streamSettings"]["realitySettings"]["settings"]["publicKey"] = ""
        self.assertEqual(inbounds.link_for(payload, "1.2.3.4"), "")


class WsInboundTests(unittest.TestCase):
    def test_payload_and_link(self):
        payload = inbounds.ws_inbound(8880, "cdn", "uuid-2", "/ol")
        self.assertEqual(payload["streamSettings"]["network"], "ws")
        self.assertEqual(payload["streamSettings"]["security"], "none")
        link = inbounds.link_for(payload, "example.com")
        self.assertIn("vless://uuid-2@example.com:8880", link)
        self.assertIn("type=ws", link)
        self.assertIn("path=%2Fol", link)


class LinkForTests(unittest.TestCase):
    def test_api_tunnel_is_ignored(self):
        self.assertEqual(inbounds.link_for({"protocol": "tunnel", "port": 62789, "settings": {}}, "h"), "")

    def test_unknown_protocol_is_ignored(self):
        self.assertEqual(inbounds.link_for({"protocol": "vmess", "settings": {}}, "h"), "")

    def test_broken_settings_are_ignored(self):
        self.assertEqual(inbounds.link_for({"protocol": "vless", "settings": {}, "port": 1}, "h"), "")

    def test_reality_link_uses_stored_dest(self):
        payload = reality()
        payload["streamSettings"]["realitySettings"]["dest"] = "www.cloudflare.com:443"
        payload["streamSettings"]["realitySettings"]["serverNames"] = ["www.cloudflare.com"]
        link = inbounds.link_for(payload, "1.2.3.4")
        self.assertIn("sni=www.cloudflare.com", link)


if __name__ == "__main__":
    unittest.main()
