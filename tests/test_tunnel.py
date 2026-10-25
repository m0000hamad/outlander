import json
import tempfile
import unittest
from pathlib import Path

from outlander import forward, tunnel


def base(*rules: dict) -> dict:
    return {"hop": {"enabled": False}, "rules": list(rules)}


class DokodemoInboundTests(unittest.TestCase):
    def test_only_dokodemo_rules_are_rendered(self):
        data = base(
            {"proto": "tcp", "listen": "2088", "target": "x.com:443", "mode": "auto", "resolved": "dokodemo"},
            {"proto": "tcp", "listen": "2089", "target": "y.com:443", "mode": "auto", "resolved": "nft"},
            {"proto": "tcp", "listen": "2090", "target": "z.com:443", "mode": "auto"},
            {"proto": "tcp", "listen": "2091", "target": "w.com:443", "mode": "nft"},
        )
        inbounds = tunnel.dokodemo_inbounds(data)
        self.assertEqual([item["port"] for item in inbounds], [2088])
        item = inbounds[0]
        self.assertEqual(item["protocol"], "dokodemo-door")
        self.assertEqual(item["settings"]["address"], "x.com")
        self.assertEqual(item["settings"]["port"], 443)
        self.assertEqual(item["settings"]["network"], "tcp")

    def test_both_becomes_tcp_udp(self):
        data = base(
            {"proto": "both", "listen": "2088", "target": "x.com:443", "mode": "dokodemo"}
        )
        self.assertEqual(tunnel.dokodemo_inbounds(data)[0]["settings"]["network"], "tcp,udp")

    def test_no_rules(self):
        self.assertEqual(tunnel.dokodemo_inbounds(base()), [])


class TunnelConfigTests(unittest.TestCase):
    def test_outbound_points_at_the_gateway(self):
        data = base({"proto": "tcp", "listen": "2088", "target": "x.com:443", "mode": "dokodemo"})
        config = tunnel.config(data, {"GATEWAY_SOCKS_PORT": "11000"})
        self.assertEqual(config["outbounds"][0]["protocol"], "socks")
        self.assertEqual(config["outbounds"][0]["settings"]["servers"][0]["port"], 11000)
        self.assertEqual(config["inbounds"][0]["port"], 2088)

    def test_render_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "forwarding.json").write_text(
                json.dumps(base({"proto": "tcp", "listen": "2088", "target": "x.com:443", "mode": "dokodemo"})),
                encoding="utf-8",
            )
            path = tunnel.render(root, {"GATEWAY_SOCKS_PORT": "10808"})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["inbounds"][0]["port"], 2088)


class EffectiveModeInTunnelTests(unittest.TestCase):
    def test_legacy_rule_not_tunnelled(self):
        data = base({"proto": "tcp", "listen": "8081", "target": "10.0.0.5:8081"})
        self.assertEqual(tunnel.dokodemo_inbounds(data), [])
        self.assertEqual(forward.effective_mode({"listen": "8081"}), "nft")


if __name__ == "__main__":
    unittest.main()
