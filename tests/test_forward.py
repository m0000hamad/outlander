import tempfile
import unittest
from pathlib import Path

from outlander import forward

PROC_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


class ListeningPortTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        listening = "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1\n"
        established = "   1: 0100007F:1F90 0100007F:9C40 01 00000000:00000000 00:00000000 00000000     0        0 2\n"
        v6 = "   0: 00000000000000000000000000000000:20FB 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 3\n"
        (root / "tcp").write_text(PROC_HEADER + listening + established, encoding="utf-8")
        (root / "tcp6").write_text(PROC_HEADER + v6, encoding="utf-8")

    def test_detects_listeners(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(forward.listening_ports(tmp), {80, 8443})

    def test_conflict_on_single_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(forward.conflicting_ports({"listen": "80"}, tmp), [80])
            self.assertEqual(forward.conflicting_ports({"listen": "2083"}, tmp), [])

    def test_conflict_ignored_with_listen_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(forward.conflicting_ports({"listen": "80", "listen_ip": "203.0.113.5"}, tmp), [])

    def test_conflict_in_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp))
            self.assertEqual(forward.conflicting_ports({"listen": "8400-8500"}, tmp), [8443])
            self.assertEqual(forward.conflicting_ports({"listen": "20000-40000"}, tmp), [])

    def test_missing_proc_net(self):
        self.assertEqual(forward.listening_ports("/definitely/not/here"), set())


class DuplicateRuleTests(unittest.TestCase):
    def test_same_proto_is_duplicate(self):
        existing = {"proto": "tcp", "listen": "2083", "listen_ip": "", "target": "10.0.0.5"}
        rule = {"proto": "tcp", "listen": "2083", "listen_ip": "", "target": "10.0.0.6"}
        self.assertIsNotNone(forward.find_duplicate([existing], rule))

    def test_both_covers_tcp(self):
        existing = {"proto": "both", "listen": "2083", "listen_ip": "", "target": "10.0.0.5"}
        rule = {"proto": "tcp", "listen": "2083", "listen_ip": "", "target": "10.0.0.6"}
        self.assertIsNotNone(forward.find_duplicate([existing], rule))

    def test_different_address_is_allowed(self):
        existing = {"proto": "tcp", "listen": "2083", "listen_ip": "", "target": "10.0.0.5"}
        rule = {"proto": "tcp", "listen": "2083", "listen_ip": "203.0.113.5", "target": "10.0.0.6"}
        self.assertIsNone(forward.find_duplicate([existing], rule))


def base(**overrides):
    data = {"hop": {"enabled": False, "proto": "udp", "range": "20000-40000", "target": "172.28.60.12:8443"}, "rules": []}
    data.update(overrides)
    return data


class RuleValidationTests(unittest.TestCase):
    def test_simple_port(self):
        rule = forward.normalise_rule({"proto": "tcp", "listen": "3389", "target": "10.0.0.5:3389"})
        self.assertEqual(rule, {"proto": "tcp", "listen": "3389", "listen_ip": "", "target": "10.0.0.5:3389"})

    def test_range_and_ip(self):
        rule = forward.normalise_rule(
            {"proto": "both", "listen": "50000-50100", "listen_ip": "203.0.113.5", "target": "10.0.0.9"}
        )
        self.assertEqual(rule["listen"], "50000-50100")
        self.assertEqual(rule["listen_ip"], "203.0.113.5")

    def test_whole_ip(self):
        rule = forward.normalise_rule(
            {"proto": "tcp", "listen": "*", "listen_ip": "203.0.113.5", "target": "10.0.0.9"}
        )
        self.assertEqual(rule["listen"], "*")

    def test_whole_ip_needs_address(self):
        with self.assertRaises(ValueError):
            forward.normalise_rule({"proto": "tcp", "listen": "*", "target": "10.0.0.9"})

    def test_whole_ip_cannot_move_port(self):
        with self.assertRaises(ValueError):
            forward.normalise_rule(
                {"proto": "tcp", "listen": "*", "listen_ip": "203.0.113.5", "target": "10.0.0.9:443"}
            )

    def test_bad_port(self):
        with self.assertRaises(ValueError):
            forward.normalise_rule({"proto": "tcp", "listen": "99999", "target": "10.0.0.5"})

    def test_bad_target(self):
        with self.assertRaises(ValueError):
            forward.normalise_rule({"proto": "tcp", "listen": "443", "target": "10.0.0.5; rm -rf /"})

    def test_bad_proto(self):
        with self.assertRaises(ValueError):
            forward.normalise_rule({"proto": "icmp", "listen": "443", "target": "10.0.0.5"})


class NftRenderTests(unittest.TestCase):
    def test_port_rule(self):
        text = forward.nft_text(base(rules=[{"proto": "tcp", "listen": "3389", "target": "10.0.0.5:3389"}]))
        self.assertIn("tcp dport 3389 dnat ip to 10.0.0.5:3389", text)

    def test_whole_ip_rule(self):
        text = forward.nft_text(
            base(rules=[{"proto": "both", "listen": "*", "listen_ip": "203.0.113.5", "target": "10.0.0.9"}])
        )
        self.assertIn("ip daddr 203.0.113.5 meta l4proto tcp dnat ip to 10.0.0.9", text)
        self.assertIn("ip daddr 203.0.113.5 meta l4proto udp dnat ip to 10.0.0.9", text)

    def test_hop(self):
        text = forward.nft_text(
            base(hop={"enabled": True, "proto": "udp", "range": "20000-40000", "target": "172.28.60.12:8443"})
        )
        self.assertIn("udp dport 20000-40000 dnat ip to 172.28.60.12:8443", text)

    def test_hop_disabled(self):
        text = forward.nft_text(base())
        self.assertNotIn("20000-40000", text)

    def test_masquerade_present(self):
        self.assertIn("ct status dnat masquerade", forward.nft_text(base()))

    def test_docker_allow_for_container_target(self):
        commands = forward.docker_allows(
            base(rules=[{"proto": "tcp", "listen": "443", "target": "172.28.60.12:8443"}])
        )
        self.assertTrue(any("DOCKER-USER" in command for command in commands))

    def test_external_target_gets_no_destination_rule(self):
        commands = forward.docker_allows(base(rules=[{"proto": "tcp", "listen": "443", "target": "10.0.0.5:443"}]))
        self.assertEqual(len(commands), 1)
        self.assertIn("--ctstate DNAT", commands[0])
        self.assertFalse(any("10.0.0.5" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
