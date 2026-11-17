import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import ops, ui
from outlander import cli, forward, gre, tunnel


def rule(**updates):
    return {"proto": "both", "listen": "28080", "target": "10.255.0.2:443", "mode": "gre",
            "gre_local": "203.0.113.1", "gre_remote": "198.51.100.1", "gre_address": "10.255.0.1", **updates}


def data(*rules):
    return {"hop": {"enabled": False}, "rules": list(rules)}


class GreValidationTests(unittest.TestCase):
    def test_invalid_addresses_and_shell_input(self):
        for field in (*gre.FIELDS, "target"):
            for value in ("", "example.com", "::1", "1.2.3.4;reboot", "0.0.0.0", "127.0.0.1", "224.0.0.1"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    forward.normalise_rule(rule(**{field: value}))
        with self.assertRaises(ValueError):
            forward.normalise_rule(rule(gre_address="10.255.0.2"))

    def test_gre_renders_dnat_for_both_protocols_without_xray_listener(self):
        text = forward.nft_text(data(rule()))
        self.assertIn("tcp dport 28080 dnat ip to 10.255.0.2:443", text)
        self.assertIn("udp dport 28080 dnat ip to 10.255.0.2:443", text)
        self.assertEqual(tunnel.dokodemo_inbounds(data(rule())), [])
        self.assertIn("ct status dnat masquerade", text)

    def test_range_and_whole_ip(self):
        text = forward.nft_text(data(rule(listen="28080-28090", target="10.255.0.2")))
        self.assertIn("udp dport 28080-28090 dnat ip to 10.255.0.2", text)
        text = forward.nft_text(data(rule(listen="*", listen_ip="203.0.113.1", target="10.255.0.2")))
        self.assertIn("meta l4proto tcp dnat ip to 10.255.0.2", text)

    def test_shared_tunnel_and_conflicting_profiles(self):
        self.assertEqual(len(gre.profiles(data(rule(), rule(listen="28443", target="10.255.0.2:8443")))), 1)
        for changed in (rule(gre_address="10.255.0.3"), rule(gre_remote="198.51.100.2")):
            with self.assertRaises(ValueError):
                gre.profiles(data(rule(), changed))

    def test_settings_change_invalidates_edit_token(self):
        self.assertNotEqual(forward.edit_token(rule()), forward.edit_token(rule(gre_remote="198.51.100.2")))

    def test_cli_accepts_gre(self):
        args = cli.build_parser().parse_args(["forward", "add", "--mode", "gre", "--listen", "28080",
            "--target", "10.255.0.2:443", "--gre-local", "203.0.113.1", "--gre-remote", "198.51.100.1",
            "--gre-address", "10.255.0.1"])
        self.assertEqual(args.mode, "gre")

    def test_bilingual_editor_preserves_settings(self):
        for lang in ("fa", "en"):
            text = ui.rule_editor({"lang": lang, "csrf": "token", "peers": []}, 0, rule())
            self.assertIn('value="gre" selected', text)
            for field in gre.FIELDS:
                self.assertIn(f'name="{field}"', text)
                self.assertIn(f'value="{rule()[field]}"', text)


class GreRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_existing_installs_do_not_require_ip(self):
        with mock.patch.object(gre, "_ip") as ip:
            gre.apply(self.root, data())
        ip.assert_not_called()

    def test_create_reapply_share_and_cleanup_preserve_unowned_links(self):
        name = next(iter(gre.profiles(data(rule()))))
        links = [{"ifname": "gre0"}, {"ifname": "unrelated", "ifalias": "something"}]
        commands = []
        def ip(*args):
            commands.append(args)
            if args == ("-j", "link", "show"):
                return json.dumps(links)
            if args[:3] == ("-j", "address", "show"):
                return "[]"
            if args[:2] == ("tunnel", "add"):
                links.append({"ifname": args[2], "ifalias": gre.OWNER + args[2]})
            return ""
        with mock.patch.object(gre, "_ip", side_effect=ip):
            gre.apply(self.root, data(rule()), cleanup=True)
            gre.apply(self.root, data(rule(), rule(listen="28443")), cleanup=True)
            gre.apply(self.root, data(), cleanup=True)
        self.assertEqual(sum(c[:2] == ("tunnel", "add") for c in commands), 1)
        self.assertIn(("address", "replace", "10.255.0.1/32", "peer", "10.255.0.2/32", "dev", name), commands)
        deletes = [c for c in commands if c[:2] == ("link", "delete")]
        self.assertEqual(deletes, [("link", "delete", "dev", name)])
        self.assertFalse((self.root / "data/gre-active").exists())

    def test_unowned_name_collision_fails_before_mutations(self):
        name = next(iter(gre.profiles(data(rule()))))
        with mock.patch.object(gre, "_ip", return_value=json.dumps([{"ifname": name}])) as ip:
            with self.assertRaises(RuntimeError):
                gre.apply(self.root, data(rule()))
        self.assertEqual(ip.call_count, 1)

    def test_failed_new_rule_restores_previous_config(self):
        original = data()
        forward.save(self.root, original)
        with mock.patch.object(ops, "ROOT", self.root), mock.patch.object(ops, "env", return_value={}), \
             mock.patch.object(forward, "conflicting_ports", return_value=[]), \
             mock.patch.object(ops, "apply_forwarding", side_effect=[(False, "GRE blocked"), (True, "ok")]):
            with self.assertRaisesRegex(Exception, "previous rule restored"):
                ops.add_gre_forward(rule())
        self.assertEqual(forward.load(self.root), original)

    def test_edit_gre_endpoint_is_applied_instead_of_metadata_only(self):
        old = forward.normalise_rule(rule())
        forward.save(self.root, data(old))
        with mock.patch.object(ops, "ROOT", self.root), mock.patch.object(ops, "env", return_value={}), \
             mock.patch.object(forward, "conflicting_ports", return_value=[]), \
             mock.patch.object(ops, "apply_forwarding", return_value=(True, "ok")) as apply:
            ops.update_forward(0, forward.edit_token(old), rule(gre_remote="198.51.100.2"))
        apply.assert_called_once()
        self.assertEqual(forward.load(self.root)["rules"][0]["gre_remote"], "198.51.100.2")


if __name__ == "__main__":
    unittest.main()
