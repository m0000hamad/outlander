import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import ops, peers, ui
from outlander import forward


class RuleEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patcher in (mock.patch.object(ops, "ROOT", self.root),
                        mock.patch.object(forward, "conflicting_ports", return_value=[]),
                        mock.patch.object(ops, "env", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.old = {"proto": "tcp", "listen": "28080", "listen_ip": "", "target": "10.1.1.1:443", "mode": "nft"}
        self.other = {"proto": "tcp", "listen": "2087", "target": "10.3.3.3:443", "mode": "dokodemo"}
        self.data = {"hop": {"enabled": False}, "rules": [self.old, self.other]}
        self.write()

    def write(self):
        forward.save(self.root, self.data)

    def edit(self, **fields):
        form = {key: self.old.get(key, "") for key in ("proto", "listen", "listen_ip", "target", "mode", "peer_id", "transport")}
        form.update(fields)
        return ops.update_forward(0, forward.edit_token(self.old), form)

    def peer_old(self):
        self.old.update(mode="peer", peer_id="a" * 12, peer_rule_id="b" * 12, transport="reality")
        self.write()

    def test_all_direct_fields_edit_in_place_without_touching_tunnel(self):
        with mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")), \
             mock.patch.object(ops, "_reload_legacy_checked") as reload:
            self.edit(proto="both", listen="28081", listen_ip="203.0.113.1", target="10.2.2.2:8443")
        data = forward.load(self.root)
        self.assertEqual(len(data["rules"]), 2)
        self.assertEqual(data["rules"][0]["listen"], "28081")
        self.assertEqual(data["rules"][0]["proto"], "both")
        self.assertEqual(data["rules"][0]["listen_ip"], "203.0.113.1")
        self.assertEqual(data["rules"][0]["target"], "10.2.2.2:8443")
        self.assertEqual(data["rules"][1], self.other)
        reload.assert_not_called()

    def test_noop_does_not_restart_or_apply(self):
        with mock.patch.object(ops, "apply_forwarding") as apply, mock.patch.object(ops, "_reload_legacy_checked") as reload:
            self.edit()
        apply.assert_not_called()
        reload.assert_not_called()

    def test_dokodemo_switch_to_peer_releases_old_listener_first(self):
        self.old["mode"] = "dokodemo"
        self.write()
        events = []
        with mock.patch.object(forward, "conflicting_ports", return_value=[28080]), \
             mock.patch.object(peers, "get", return_value={"transports": {"xhttp": {"status": "ready"}}}), \
             mock.patch.object(ops, "_reload_legacy_checked", side_effect=lambda *a: events.append("reload")), \
             mock.patch.object(peers, "create_forward", side_effect=lambda *a: events.append("create")), \
             mock.patch.object(ops, "apply_forwarding", side_effect=lambda: (events.append("firewall") or True, "")):
            self.edit(mode="peer", peer_id="a" * 12, transport="xhttp")
        self.assertEqual(events, ["reload", "create", "firewall"])
        rule = forward.load(self.root)["rules"][0]
        self.assertEqual((rule["mode"], rule["transport"]), ("peer", "xhttp"))

    def test_peer_to_dokodemo_removes_only_old_managed_container(self):
        self.peer_old()
        with mock.patch.object(peers, "delete_forward") as delete, \
             mock.patch.object(ops, "_reload_legacy_checked") as reload, \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")):
            self.edit(mode="dokodemo")
        delete.assert_called_once_with(self.root, self.old)
        reload.assert_called_once()
        self.assertEqual(forward.load(self.root)["rules"][0]["mode"], "dokodemo")

    def test_peer_edit_replaces_only_its_container_without_legacy_restart(self):
        self.peer_old()
        with mock.patch.object(peers, "get", return_value={"transports": {"ssh": {"status": "ready"}}}), \
             mock.patch.object(peers, "delete_forward") as delete, mock.patch.object(peers, "create_forward") as create, \
             mock.patch.object(ops, "_reload_legacy_checked") as reload, \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")):
            self.edit(peer_id="c" * 12, transport="ssh", target="10.4.4.4:80")
        delete.assert_called_once()
        create.assert_called_once()
        reload.assert_not_called()
        rule = forward.load(self.root)["rules"][0]
        self.assertEqual(rule["peer_rule_id"], self.old["peer_rule_id"])
        self.assertEqual(rule["peer_id"], "c" * 12)
        self.assertEqual(rule["transport"], "ssh")

    def test_failed_firewall_apply_restores_original_rule_and_runtime(self):
        original = copy.deepcopy(self.data)
        with mock.patch.object(ops, "_reload_legacy_checked") as reload, \
             mock.patch.object(ops, "apply_forwarding", side_effect=[(False, "failed"), (True, "restored")]):
            with self.assertRaisesRegex(peers.PeerError, "previous rule restored"):
                self.edit(mode="dokodemo")
        self.assertEqual(forward.load(self.root), original)
        self.assertEqual(reload.call_count, 2)
        self.assertEqual(reload.call_args.args[0], original)

    def test_failed_peer_creation_recreates_old_peer(self):
        self.peer_old()
        original = copy.deepcopy(self.data)
        with mock.patch.object(peers, "get", return_value={"transports": {"reality": {"status": "ready"}}}), \
             mock.patch.object(peers, "delete_forward"), \
             mock.patch.object(peers, "create_forward", side_effect=[peers.PeerError("failed"), None]) as create, \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")):
            with self.assertRaisesRegex(peers.PeerError, "previous rule restored"):
                self.edit(target="10.9.9.9:443")
        self.assertEqual(forward.load(self.root), original)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args.args[1], self.old)

    def test_port_conflict_and_invalid_fields_leave_original_untouched(self):
        for values in ({"listen": "2087"}, {"target": "bad;command"}, {"mode": "peer", "peer_id": "bad"}):
            with self.subTest(values=values), mock.patch.object(ops, "apply_forwarding") as apply:
                with self.assertRaises(ValueError):
                    self.edit(**values)
                apply.assert_not_called()
            self.assertEqual(forward.load(self.root), self.data)

    def test_stale_index_token_is_rejected(self):
        self.data["rules"].reverse()
        self.write()
        with self.assertRaisesRegex(peers.PeerError, "Rule changed"):
            self.edit(target="10.9.9.9:443")
        self.assertEqual(forward.load(self.root), self.data)

    def test_auto_health_changes_do_not_stale_editor(self):
        self.old.update(mode="auto", resolved="nft", checked_at="one")
        self.write()
        token = forward.edit_token(self.old)
        self.old.update(checked_at="two", last_error="health changed")
        self.write()
        self.assertEqual(token, forward.edit_token(self.old))
        with mock.patch.object(ops, "_decide", return_value=("dokodemo", "")), \
             mock.patch.object(ops, "_reload_legacy_checked"), mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")):
            self.edit(target="10.5.5.5:443")
        self.assertEqual(forward.load(self.root)["rules"][0]["resolved"], "dokodemo")

    def test_running_tunnel_without_expected_listener_is_not_accepted(self):
        self.old["mode"] = "dokodemo"
        with mock.patch.object(ops, "apply_tunnel", return_value=(True, "ok")), \
             mock.patch.object(ops, "container_state", return_value={"state": "running"}), \
             mock.patch.object(forward, "listening_ports", return_value=set()), mock.patch.object(ops.time, "sleep"):
            with self.assertRaisesRegex(peers.PeerError, "listener did not start"):
                ops._reload_legacy_checked(self.data, {})

    def test_unreachable_auto_edit_keeps_current_working_rule(self):
        with mock.patch.object(ops, "_decide", return_value=("", "both failed")), \
             mock.patch.object(ops, "apply_forwarding") as apply:
            with self.assertRaisesRegex(peers.PeerError, "previous rule unchanged"):
                self.edit(mode="auto")
        apply.assert_not_called()
        self.assertEqual(forward.load(self.root), self.data)

    def test_editor_has_prefilled_fields_and_stale_form_token(self):
        page = ui.rule_editor({"lang": "en", "csrf": "csrf-value", "peers": []}, 0, self.old)
        for name in ("proto", "listen", "listen_ip", "target", "mode", "peer_id", "transport"):
            self.assertIn(f'name="{name}"', page)
        self.assertIn('value="28080"', page)
        self.assertIn('value="10.1.1.1:443"', page)
        self.assertIn(forward.edit_token(self.old), page)
        self.assertIn('action="/forwarding/update"', page)


if __name__ == "__main__":
    unittest.main()
