import json
import unittest

from outlander import panel

OUTBOUND = {"tag": "ol-gateway", "protocol": "socks", "settings": {"servers": [{"address": "127.0.0.1", "port": 10808}]}}
RULE = {"type": "field", "network": "tcp,udp", "outboundTag": "ol-gateway"}


class MergeTests(unittest.TestCase):
    def config(self):
        return {
            "log": {"loglevel": "warning"},
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "blocked", "protocol": "blackhole"},
            ],
            "routing": {"domainStrategy": "AsIs", "rules": [{"ip": ["geoip:private"], "outboundTag": "blocked"}]},
        }

    def test_adds_outbound_and_rule(self):
        merged = panel.merge_config(self.config(), OUTBOUND, RULE)
        self.assertEqual([o["tag"] for o in merged["outbounds"]], ["direct", "blocked", "ol-gateway"])
        self.assertEqual(merged["routing"]["rules"][-1], RULE)
        self.assertEqual(merged["routing"]["domainStrategy"], "AsIs")

    def test_idempotent(self):
        once = panel.merge_config(self.config(), OUTBOUND, RULE)
        twice = panel.merge_config(json.loads(json.dumps(once)), OUTBOUND, RULE)
        self.assertEqual(once, twice)

    def test_keeps_existing_rules_and_keys(self):
        merged = panel.merge_config(self.config(), OUTBOUND, RULE)
        self.assertEqual(merged["routing"]["rules"][0]["outboundTag"], "blocked")
        self.assertIn("log", merged)

    def test_missing_routing_is_created(self):
        merged = panel.merge_config({"outbounds": []}, OUTBOUND, RULE)
        self.assertEqual(merged["routing"]["rules"], [RULE])
        self.assertEqual(merged["routing"]["domainStrategy"], "AsIs")


if __name__ == "__main__":
    unittest.main()
