import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import traffic, ui
from outlander import forward


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rule = {"proto": "tcp", "listen": "28080", "target": "10.1.1.1:443", "mode": "auto"}
        forward.save(self.root, {"rules": [self.rule]})
        self.key = traffic.rule_id(self.rule)

    def test_identity_survives_transport_switch_but_not_target_edit(self):
        switched = dict(self.rule, resolved="peer", peer_id="a"*12, peer_rule_id="b"*12, transport="ssh")
        self.assertEqual(self.key, traffic.rule_id(switched))
        self.assertNotEqual(self.key, traffic.rule_id(dict(self.rule, target="10.2.2.2:443")))

    def test_rules_cover_local_and_nat_directions_without_dropping_packets(self):
        rule = dict(self.rule, mode="nft", proto="both", listen="28080-28090", listen_ip="203.0.113.3")
        text = traffic.ruleset([rule], "test-epoch", False)
        self.assertNotIn("delete table", text)
        for hook in ("input", "output", "forward"):
            self.assertIn(f"hook {hook} priority 10; policy accept", text)
        self.assertEqual(text.count("counter name up_"), 2)
        self.assertEqual(text.count("counter name down_"), 2)
        self.assertIn("ct original proto-dst 28080-28090", text)
        self.assertIn("ct original ip daddr 203.0.113.3", text)
        self.assertIn("ct direction reply ct status dnat", text)
        self.assertNotIn(" drop", text)

    def test_wildcard_does_not_create_invalid_port_expression(self):
        rule = dict(self.rule, mode="nft", listen="*", listen_ip="203.0.113.3", target="10.1.1.1")
        self.assertNotIn("proto-dst", traffic.ruleset([rule], "epoch", True))

    def test_accumulate_restarts_and_counter_resets(self):
        state = {"entries": {}}
        for now, epoch, up, down in [(1, "a", 100, 200), (2, "a", 150, 260), (3, "a", 150, 260)]:
            traffic._accumulate(state, epoch, {self.key: {"up": up, "down": down}}, now)
        self.assertEqual(state["entries"][self.key]["up"], 150)
        # New epoch resets even when the new raw value exceeds the old one.
        traffic._accumulate(state, "b", {self.key: {"up": 170, "down": 300}}, 4)
        self.assertEqual(state["entries"][self.key]["up"], 320)
        traffic._accumulate(state, "b", {self.key: {"up": 5, "down": 10}}, 5)
        self.assertEqual(state["entries"][self.key]["up"], 325)
        self.assertEqual(state["entries"][self.key]["since"], 1)

    def test_sample_creates_table_and_preserves_totals_after_reboot(self):
        with mock.patch.object(traffic, "_read", return_value=None), mock.patch.object(traffic, "_nft"):
            initial = traffic.sample(self.root)
        self.assertTrue(initial["available"])
        self.assertEqual(initial["rules"][self.key]["up"], 0)
        path = self.root / "data/console/forward-traffic.json"
        epoch = json.loads(path.read_text())["epoch"]
        with mock.patch.object(traffic, "_read", return_value=(epoch, {self.key: {"up": 200, "down": 300}})), mock.patch.object(traffic, "_nft") as nft:
            result = traffic.sample(self.root)
            nft.assert_not_called()
        self.assertEqual(result["rules"][self.key]["up"], 200)
        with mock.patch.object(traffic, "_read", return_value=None), mock.patch.object(traffic, "_nft"):
            result = traffic.sample(self.root)
        self.assertEqual(result["rules"][self.key]["up"], 200)

    def test_failed_read_never_resets_or_replaces_counters(self):
        with mock.patch.object(traffic, "_read", side_effect=RuntimeError("permission denied")), mock.patch.object(traffic, "_nft") as nft:
            self.assertFalse(traffic.sample(self.root)["available"])
            nft.assert_not_called()

    def test_read_named_counter_json(self):
        data = {"nftables": [{"table": {"comment": "epoch"}}, {"counter": {"name": "up_"+self.key, "bytes": 123}}]}
        with mock.patch.object(traffic.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=json.dumps(data))):
            self.assertEqual(traffic._read(), ("epoch", {self.key: {"up": 123}}))

    def test_endpoint_requires_login_and_disables_cache(self):
        from console import app
        class Request:
            path = "/forwarding/traffic"
            _session = mock.Mock(return_value=None)
            _redirect = mock.Mock()
            _send = mock.Mock()
        request = Request()
        with mock.patch.object(traffic, "sample", return_value={"available": True}) as sample:
            app.Handler.do_GET(request)
            sample.assert_not_called()
            request._redirect.assert_called_once_with("/login")
            request._session.return_value = "valid-session"
            app.Handler.do_GET(request)
            self.assertEqual(request._send.call_args.kwargs["headers"]["Cache-Control"], "no-store")

    def test_controls_refresh_values_without_replacing_edit_forms(self):
        script = ui.traffic_script("fa")
        self.assertIn("/forwarding/traffic", script)
        self.assertIn("addEventListener('click',refresh)", script)
        self.assertIn("},5000)", script)
        self.assertIn("auto.checked&&!document.hidden", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("location.reload", script)


if __name__ == "__main__":
    unittest.main()
