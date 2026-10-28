import unittest
from unittest import mock

from console import ops
from outlander import forward


class EffectiveModeTests(unittest.TestCase):
    def test_legacy_rule_is_nft(self):
        self.assertEqual(forward.effective_mode({"listen": "8081"}), "nft")

    def test_fixed_modes(self):
        self.assertEqual(forward.effective_mode({"mode": "nft"}), "nft")
        self.assertEqual(forward.effective_mode({"mode": "dokodemo"}), "dokodemo")

    def test_auto_uses_resolution(self):
        self.assertEqual(forward.effective_mode({"mode": "auto"}), "")
        self.assertEqual(forward.effective_mode({"mode": "auto", "resolved": "nft"}), "nft")


class DecideTests(unittest.TestCase):
    def decide(self, direct: bool, tunnel: bool) -> tuple[str, str]:
        with mock.patch.object(ops.forward, "resolve_target", return_value="1.2.3.4:443"), mock.patch.object(
            ops.probe, "tcp_reachable", return_value=direct
        ), mock.patch.object(ops.probe, "socks5_reachable", return_value=tunnel), mock.patch.object(
            ops, "_gateway", return_value=("127.0.0.1", 10808)
        ), mock.patch.object(ops.probe, "socks5_http_reachable", return_value=tunnel), mock.patch.object(
            ops, "_ready_peer_routes", return_value=[]
        ):
            return ops._decide({"target": "x.example:443"})

    def test_direct_is_excluded_even_when_reachable(self):
        self.assertEqual(self.decide(True, True), ("dokodemo", ""))
        self.assertEqual(self.decide(True, False)[0], "")

    def test_tunnel_fallback(self):
        self.assertEqual(self.decide(False, True), ("dokodemo", ""))

    def test_both_fail(self):
        mode, detail = self.decide(False, False)
        self.assertEqual(mode, "")
        self.assertTrue(detail)

    def test_target_without_port(self):
        with mock.patch.object(ops.forward, "resolve_target", return_value="1.2.3.4"):
            self.assertEqual(ops._decide({"target": "x.example"})[0], "")


if __name__ == "__main__":
    unittest.main()
