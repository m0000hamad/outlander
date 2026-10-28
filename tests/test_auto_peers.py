import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import ops, peers, ui
from outlander import forward, probe, tunnel


class AutoPeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(ops, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.peer = {"id": "a" * 12, "name": "Peer A", "host": "203.0.113.10", "transports": {
            kind: {"status": "ready", "port": 24443, "sni": "example.com", "uuid": "test-uuid",
                   "public_key": "PUBLIC", "private_key": "PRIVATE", "short_id": "abcd", "path": "/test"}
            for kind in ("reality", "xhttp", "ssh")}}
        peers._save(self.root, {"instance": "c" * 12, "servers": [self.peer]})
        self.rule = {"proto": "both", "listen": "2083", "target": "10.0.0.5:443", "mode": "auto", "resolved": "nft"}
        self.fixed = {"proto": "tcp", "listen": "2087", "target": "10.0.0.8:443", "mode": "dokodemo"}
        forward.save(self.root, {"hop": {"enabled": False}, "rules": [self.rule, self.fixed]})

    def decision(self, direct=False, results=None, gateway=False, gateway_http=False, rule=None):
        outcomes = iter(results or [])
        def measured(*args):
            value = next(outcomes, None)
            return .2 if value is True else None if value is False else value
        with mock.patch.object(forward, "resolve_target", return_value="10.0.0.5:443"), \
             mock.patch.object(probe, "tcp_reachable", return_value=direct), \
             mock.patch.object(peers, "measure_target", side_effect=measured) as check, \
             mock.patch.object(probe, "socks5_reachable", return_value=gateway), \
             mock.patch.object(probe, "socks5_http_reachable", return_value=gateway_http):
            selected = ops._decide(rule or self.rule)
        return selected, check

    def test_direct_is_never_an_automatic_candidate(self):
        selected, check = self.decision(direct=True)
        self.assertEqual(selected[0], "")
        self.assertEqual(check.call_count, 2)

    def test_auto_range_cannot_silently_become_direct(self):
        with self.assertRaises(peers.PeerError):
            ops._edited_rule({}, self.rule | {'listen': '2083-2084'})

    def test_reality_failure_falls_through_to_xhttp(self):
        selected, check = self.decision(results=[False, True])
        self.assertEqual(selected[0], "peer:" + self.peer["id"] + ":xhttp")
        self.assertEqual([call.args[2] for call in check.call_args_list], ["reality", "xhttp"])

    def test_ready_only_and_ssh_protocol_compatibility(self):
        self.peer["transports"]["reality"]["status"] = "installing"
        peers._save(self.root, {"instance": "c" * 12, "servers": [self.peer]})
        self.assertEqual([kind for _, kind in ops._ready_peer_routes(self.rule)], ["xhttp"])
        self.assertEqual([kind for _, kind in ops._ready_peer_routes(self.rule | {"proto": "tcp"})], ["xhttp", "ssh"])

    def test_selected_healthy_peer_is_checked_first(self):
        rule = self.rule | {"resolved": "peer", "peer_id": self.peer["id"], "transport": "xhttp"}
        selected, check = self.decision(results=[True], rule=rule)
        self.assertEqual(selected[0], "peer:" + self.peer["id"] + ":xhttp")
        self.assertEqual(check.call_args_list[0].args[2], "xhttp")

    def test_gateway_requires_real_traffic_not_just_socks_ack(self):
        self.assertEqual(self.decision(gateway=True, gateway_http=True)[0][0], "dokodemo")
        self.assertEqual(self.decision(gateway=True, gateway_http=False)[0][0], "")

    def test_peer_dns_can_work_when_local_resolution_fails(self):
        with mock.patch.object(forward, "resolve_target", side_effect=ValueError("DNS failed")), \
             mock.patch.object(peers, "measure_target", return_value=.2) as check, \
             mock.patch.object(probe, "socks5_http_reachable", return_value=False):
            selected = ops._decide(self.rule | {"target": "destination.example:443"})
        self.assertTrue(selected[0].endswith(":reality"))
        self.assertEqual(check.call_args.args[3], "destination.example")

    def test_faster_tls_tunnel_wins_within_security_tier(self):
        selected, _ = self.decision(results=[.6, .2])
        self.assertTrue(selected[0].endswith(':xhttp'))
        self.assertEqual(self.rule['auto_latency_ms'], 200)

    def test_camouflaged_tunnel_preferred_over_faster_ssh_and_gateway(self):
        selected, _ = self.decision(results=[.8, None, .01], gateway=True, gateway_http=True,
                                    rule=self.rule | {'proto': 'tcp'})
        self.assertTrue(selected[0].endswith(':reality'))

    def test_small_improvement_keeps_current_route_but_large_improvement_switches(self):
        rule = self.rule | {'resolved': 'peer', 'peer_id': self.peer['id'], 'transport': 'xhttp'}
        self.assertTrue(self.decision(results=[.3, .26], rule=rule)[0][0].endswith(':xhttp'))
        self.assertTrue(self.decision(results=[.3, .15], rule=rule)[0][0].endswith(':reality'))

    def test_unhealthy_current_switches_immediately(self):
        rule = self.rule | {'resolved': 'peer', 'peer_id': self.peer['id'], 'transport': 'xhttp'}
        self.assertTrue(self.decision(results=[None, .2], rule=rule)[0][0].endswith(':reality'))

    def test_runtime_metadata_survives_render_without_staling_form(self):
        rule = copy.deepcopy(self.rule)
        token = forward.edit_token(rule)
        ops._assign_decision(rule, "peer:" + self.peer["id"] + ":xhttp")
        normalized = forward.normalise_rule(rule)
        self.assertEqual(forward.effective_mode(normalized), "peer")
        self.assertEqual(normalized["peer_name"], "Peer A")
        self.assertEqual(forward.edit_token(normalized), token)
        self.assertEqual(tunnel.config({"rules": [normalized]})["inbounds"], [])
        self.assertIn("Peer A", ui._mode_label(normalized, "en"))
        self.assertIn("XHTTP", ui._mode_label(normalized, "en"))
        runtime_id = normalized["auto_rule_id"]
        ops._assign_decision(normalized, "nft")
        self.assertNotIn("peer_id", normalized)
        self.assertEqual(normalized["auto_rule_id"], runtime_id)

    def test_automatic_peer_and_direct_switches_preserve_fixed_rules(self):
        with mock.patch.object(ops, "_decide", return_value=("peer:" + self.peer["id"] + ":xhttp", "")), \
             mock.patch.object(peers, "create_forward") as create, \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")), \
             mock.patch.object(ops, "_reload_legacy_checked") as legacy:
            ops.reconcile_forwards()
        selected = forward.load(self.root)["rules"][0]
        self.assertEqual(selected["mode"], "auto")
        self.assertEqual(selected["resolved"], "peer")
        create.assert_called_once()
        legacy.assert_not_called()
        with mock.patch.object(ops, "_decide", return_value=("nft", "")), \
             mock.patch.object(peers, "delete_forward") as delete, \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")), \
             mock.patch.object(ops, "_reload_legacy_checked") as legacy:
            ops.reconcile_forwards()
        delete.assert_called_once_with(self.root, selected)
        legacy.assert_not_called()
        self.assertEqual(forward.load(self.root)["rules"][1], self.fixed)

    def test_failed_peer_start_restores_previous_dokodemo_path(self):
        self.rule["resolved"] = "dokodemo"
        forward.save(self.root, {"hop": {"enabled": False}, "rules": [self.rule, self.fixed]})
        with mock.patch.object(ops, "_decide", return_value=("peer:" + self.peer["id"] + ":reality", "")), \
             mock.patch.object(peers, "create_forward", side_effect=peers.PeerError("failed start")), \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")), \
             mock.patch.object(ops, "_reload_legacy_checked") as legacy:
            self.assertEqual(ops.reconcile_forwards(), "")
        data = forward.load(self.root)
        self.assertEqual(data["rules"][0]["resolved"], "dokodemo")
        self.assertIn("previous rule restored", data["rules"][0]["last_error"])
        self.assertEqual(data["rules"][1], self.fixed)
        self.assertEqual(legacy.call_count, 2)

    def test_all_candidates_down_retains_current_selection(self):
        self.rule['resolved'] = 'dokodemo'
        forward.save(self.root, {'rules': [self.rule, self.fixed]})
        with mock.patch.object(ops, "_decide", return_value=("", "no healthy routes")), \
             mock.patch.object(ops, "_apply_forward_change") as change:
            ops.reconcile_forwards()
        change.assert_not_called()
        self.assertEqual(forward.load(self.root)["rules"][0]["resolved"], "dokodemo")

    def test_old_auto_direct_is_disabled_when_no_tunnel_is_healthy(self):
        with mock.patch.object(ops, '_decide', return_value=('', 'no healthy tunnels')), \
             mock.patch.object(ops, 'apply_forwarding', return_value=(True, 'ok')), \
             mock.patch.object(ops, '_reload_legacy_checked') as legacy:
            ops.reconcile_forwards()
        rules = forward.load(self.root)['rules']
        self.assertEqual(rules[0]['resolved'], '')
        self.assertEqual(rules[0]['last_error'], 'no healthy tunnels')
        self.assertEqual(rules[1], self.fixed)
        self.assertNotIn('dport 2083 dnat', forward.nft_text({'rules': rules}))
        legacy.assert_not_called()

    def test_same_route_health_refresh_does_not_restart(self):
        with mock.patch.object(ops, "_decide", return_value=("nft", "")), \
             mock.patch.object(ops, "_apply_forward_change") as change:
            ops.reconcile_forwards()
        change.assert_not_called()

    def test_probe_always_cleans_up_and_rejects_optimistic_ack(self):
        with mock.patch.object(peers, "create_forward") as create, \
             mock.patch.object(peers, "delete_forward") as delete, \
             mock.patch.object(probe, "socks5_http_reachable", return_value=False), \
             mock.patch.object(probe, "socks5_reachable", return_value=True) as destination:
            self.assertFalse(peers.probe_target(self.root, self.peer, "xhttp", "10.0.0.5", 443))
        self.assertTrue(create.call_args.kwargs["probe_only"])
        delete.assert_called_once()
        destination.assert_not_called()

    def test_real_probe_config_is_loopback_only_and_not_restarted(self):
        rule = {"mode": "peer", "peer_id": self.peer["id"], "peer_rule_id": "b" * 12,
                "transport": "xhttp", "proto": "tcp", "listen": "29001", "target": "10.0.0.5:443"}
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            return (201, b"{}") if "/create" in path else (204, b"")
        with mock.patch.object(peers, "_owned", side_effect=[None, {"State": {"Running": True}}]), \
             mock.patch.object(peers, "api", side_effect=api), \
             mock.patch.object(peers, "_host_path", return_value="/private/qa"), \
             mock.patch.object(peers.os, "chown", create=True), \
             mock.patch.object(peers.socket, "create_connection"):
            peers.create_forward(self.root, rule, probe_only=True)
        config = json.loads((self.root / "data/peers/forwards" / ("b" * 12) / "config.json").read_text())
        self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(config["inbounds"][0]["protocol"], "socks")
        payload = calls[0][2]
        self.assertEqual(payload["Labels"]["outlander.peer.probe"], "true")
        self.assertEqual(payload["HostConfig"]["RestartPolicy"]["Name"], "no")
        self.assertFalse(any("ol-tunnel" in path for _, path, _ in calls))

    def test_ssh_probe_uses_dynamic_forward_without_extra_container(self):
        with mock.patch.object(peers, "ensure_ssh", return_value=29002), \
             mock.patch.object(probe, "socks5_reachable", return_value=True), \
             mock.patch.object(probe, "socks5_http_reachable", return_value=True), \
             mock.patch.object(peers, "create_forward") as create:
            self.assertTrue(peers.probe_target(self.root, self.peer, "ssh", "10.0.0.5", 443))
        create.assert_not_called()

    def test_measurement_excludes_container_startup_and_requires_two_successes(self):
        clock = [0.0]
        def create(*args, **kwargs):
            clock[0] += 30
        def http(*args):
            clock[0] += .10
            return True
        def destination(*args):
            clock[0] += .05
            return True
        with mock.patch.object(peers, 'create_forward', side_effect=create), \
             mock.patch.object(peers, 'delete_forward'), \
             mock.patch.object(peers.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(probe, 'socks5_http_reachable', side_effect=http), \
             mock.patch.object(probe, 'socks5_reachable', side_effect=destination):
            self.assertAlmostEqual(peers.measure_target(self.root, self.peer, 'xhttp', '10.0.0.5', 443), .15)
        with mock.patch.object(peers, 'create_forward'), mock.patch.object(peers, 'delete_forward'), \
             mock.patch.object(probe, 'socks5_http_reachable', side_effect=[True, False]), \
             mock.patch.object(probe, 'socks5_reachable', return_value=True):
            self.assertIsNone(peers.measure_target(self.root, self.peer, 'xhttp', '10.0.0.5', 443))


class HTTPProbeTests(unittest.TestCase):
    def test_socks_ack_without_http_response_is_not_healthy(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.makefile.return_value.__enter__.return_value = io.BytesIO(b"")
        with mock.patch.object(probe, "_socks_connect", return_value=sock):
            self.assertFalse(probe.socks5_http_reachable("127.0.0.1", 1080, "http://example.com/health"))

    def test_actual_success_response_is_required(self):
        for status, expected in ((b"HTTP/1.1 204 No Content\r\n", True), (b"HTTP/1.1 503 Unavailable\r\n", False)):
            sock = mock.MagicMock()
            sock.__enter__.return_value = sock
            sock.makefile.return_value.__enter__.return_value = io.BytesIO(status)
            with self.subTest(status=status), mock.patch.object(probe, "_socks_connect", return_value=sock):
                self.assertEqual(probe.socks5_http_reachable("127.0.0.1", 1080, "http://example.com/health"), expected)


if __name__ == "__main__":
    unittest.main()
