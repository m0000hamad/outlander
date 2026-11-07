import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import ops, traffic, ui
from outlander import forward


class ForwardNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.object(ops, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.rule = {"proto": "tcp", "listen": "28080", "listen_ip": "", "target": "10.1.1.1:443", "mode": "nft"}

    def test_optional_name_normalizes_and_rejects_invalid_values(self):
        self.assertNotIn("name", forward.normalise_rule(self.rule))
        self.assertEqual(forward.normalise_rule(dict(self.rule, name="  سرور آلمان  "))["name"], "سرور آلمان")
        for name in ("a"*101, "a\nb", 123):
            with self.subTest(name=name), self.assertRaises(ValueError):
                forward.normalise_rule(dict(self.rule, name=name))

    def test_name_only_edits_do_not_touch_any_route_or_usage_identity(self):
        for mode in ("nft", "dokodemo", "peer", "auto"):
            with self.subTest(mode=mode):
                old = dict(self.rule, mode=mode, resolved="peer" if mode=="auto" else mode,
                           peer_id="a"*12, peer_rule_id="b"*12, transport="ssh", checked_at="unchanged")
                forward.save(self.root, {"rules": [old]})
                form = {key: old[key] for key in ("proto", "listen", "listen_ip", "target", "mode", "peer_id", "transport")}
                with mock.patch.object(ops, "_edited_rule") as edit, mock.patch.object(ops, "_apply_forward_change") as apply:
                    ops.update_forward(0, forward.edit_token(old), form | {"name": "سرور اصلی"})
                    edit.assert_not_called()
                    apply.assert_not_called()
                saved = forward.load(self.root)["rules"][0]
                self.assertEqual(saved, old | {"name": "سرور اصلی"})
                self.assertEqual(traffic.rule_id(saved), traffic.rule_id(old))

    def test_clear_name_and_reject_stale_or_invalid_edit(self):
        old = dict(self.rule, name="Germany")
        forward.save(self.root, {"rules": [old]})
        form = self.rule | {"name": ""}
        ops.update_forward(0, forward.edit_token(old), form)
        saved = forward.load(self.root)["rules"][0]
        self.assertNotIn("name", saved)
        with self.assertRaisesRegex(Exception, "Rule changed"):
            ops.update_forward(0, forward.edit_token(old), form | {"name": "new"})
        with self.assertRaises(ValueError):
            ops.update_forward(0, forward.edit_token(saved), form | {"name": "x"*101})
        self.assertEqual(forward.load(self.root)["rules"][0], saved)

    def test_regular_edit_keeps_name_if_older_caller_omits_it(self):
        old = dict(self.rule, name="Germany")
        with mock.patch.object(ops, "_assert_not_self_forward"):
            result = ops._edited_rule(old, self.rule | {"target": "10.2.2.2:443"})
        self.assertEqual(result["name"], "Germany")

    def test_name_saved_for_manual_create(self):
        with mock.patch.object(forward, "conflicting_ports", return_value=[]), mock.patch.object(ops, "_assert_not_self_forward"):
            ops.add_forward("tcp", "", "28080", "10.1.1.1:443", name="آلمان")
        self.assertEqual(forward.load(self.root)["rules"][0]["name"], "آلمان")

    def test_automatic_create_keeps_name(self):
        with mock.patch.object(ops, "_decide", return_value=("nft", "")), mock.patch.object(ops, "_assert_not_self_forward"):
            rule = ops._edited_rule({}, self.rule | {"mode": "auto", "name": "Auto Germany"})
        self.assertEqual(rule["name"], "Auto Germany")

    def test_table_and_editor_escape_names(self):
        name = '<script>alert("x")</script>'
        ctx = {"lang": "en", "csrf": "test", "hop": {}, "rules": [self.rule | {"name": name}], "peers": [], "flash": ""}
        page = ui.forwarding(ctx)
        self.assertIn('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;', page)
        self.assertNotIn(name, page)
        self.assertEqual(page.count('name="name"'), 2)
        self.assertIn('colspan="10"', page)


if __name__ == "__main__":
    unittest.main()
