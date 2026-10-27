import json
import os
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from console import peers, ops, ui
from console import app
from outlander import forward, tunnel


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.form = {"name": "Peer <A>", "host": "203.0.113.12", "username": "root", "password": "not-in-output"}

    def add(self):
        return peers.save_server(self.root, self.form)

    def profile(self):
        return {"status": "ready", "port": 24443, "sni": "www.example.com", "uuid": "uuid-value",
                "private_key": "PRIVATE", "public_key": "PUBLIC", "short_id": "abcd", "path": "/test"}

    def rule(self, peer_id, **overrides):
        return {"mode": "peer", "peer_id": peer_id, "peer_rule_id": "123456abcdef", "transport": "reality",
                "proto": "tcp", "listen": "2099", "target": "10.0.0.1:443", **overrides}

    def test_credentials_are_private_and_not_exposed(self):
        peer_id = self.add()
        peers._profile(self.root, peer_id, "reality", self.profile())
        public = json.dumps(peers.public(self.root))
        self.assertNotIn("not-in-output", public)
        for value in ("PRIVATE", "PUBLIC", "uuid-value", "abcd"):
            self.assertNotIn(value, public)
        if os.name != "nt":
            self.assertEqual((peers.directory(self.root) / "servers.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(peers.directory(self.root).stat().st_mode & 0o777, 0o700)

    def test_input_cannot_inject_ssh_options_or_shell_commands(self):
        for field, value in (("host", "-oProxyCommand=evil"), ("host", "a;whoami"), ("username", "root;id")):
            with self.subTest(field=field), self.assertRaises(peers.PeerError):
                peers.save_server(self.root, self.form | {field: value})
        with self.assertRaises(peers.PeerError):
            peers.save_server(self.root, self.form | {"private_key": "secret"})
        with self.assertRaises(peers.PeerError):
            peers.save_server(self.root, self.form | {"password": "abc\nxyz"})

    def test_host_keys_are_pinned_and_not_disabled(self):
        peer_id = self.add()
        args = peers._ssh_command(self.root, peers.get(self.root, peer_id))
        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertTrue(any("known_hosts" in arg for arg in args))
        self.assertNotIn("not-in-output", " ".join(args))
        self.assertNotIn("StrictHostKeyChecking=no", args)

    def test_remote_config_and_client_config_match(self):
        profile = self.profile()
        peer = {"host": "203.0.113.12", "transports": {"reality": profile, "xhttp": profile}}
        for kind in ("reality", "xhttp"):
            server = peers.server_config(profile, kind)["inbounds"][0]
            client = peers.forward_config(self.rule("a" * 12, transport=kind), peer)["outbounds"][0]
            self.assertEqual(client["settings"]["vnext"][0]["port"], server["port"])
            self.assertEqual(client["streamSettings"]["network"], server["streamSettings"]["network"])
            self.assertEqual(client["streamSettings"]["realitySettings"]["password"], profile["public_key"])
            self.assertNotIn("PRIVATE", json.dumps(client))
            self.assertEqual(client["streamSettings"]["sockopt"]["tcpMaxSeg"], 1200)
        self.assertNotIn("flow", peers.server_config(profile, "xhttp")["inbounds"][0]["settings"]["clients"][0])
        allowed = peers.server_config(profile, "reality")["outbounds"][0]["settings"]["finalRules"][0]["ip"]
        self.assertIn("127.0.0.0/8", allowed)
        self.assertNotIn("169.254.0.0/16", allowed)

    def test_legacy_tunnel_config_unchanged_by_peer_rules(self):
        legacy = {"proto": "both", "listen": "2087", "target": "10.0.0.1:443", "mode": "dokodemo"}
        before = tunnel.config({"rules": [legacy]})
        after = tunnel.config({"rules": [legacy, self.rule("a" * 12)]})
        self.assertEqual(before, after)
        self.assertNotIn("dport 2099 dnat", forward.nft_text({"rules": [self.rule("a" * 12)]}))
        self.assertIn("tcp sport { 2099 }", forward.nft_text({"rules": [self.rule("a" * 12)]}))

    def test_ssh_rejects_udp_and_ranges(self):
        for overrides in ({"transport": "ssh", "proto": "both"}, {"listen": "2000-2010"}, {"listen_ip": "1.2.3.4"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                forward.normalise_rule(self.rule("a" * 12, **overrides))

    def test_remote_provision_is_namespaced_and_validates_before_start(self):
        script = peers.provision_script("a" * 12, "b" * 12, self.profile(), "reality")
        self.assertNotIn("ol-gateway", script)
        self.assertNotIn("ol-panel", script)
        self.assertNotIn("ol-tunnel", script)
        self.assertNotIn("docker compose", script)
        self.assertIn("outlander.peer.instance", script)
        self.assertLess(script.index("-test -c"), script.index("docker run -d"))
        self.assertIn("OUTLANDER_ERROR:port_busy", script)
        privileged = peers.provision_script("a" * 12, "b" * 12, self.profile() | {"port": 443}, "reality")
        self.assertIn("--cap-drop ALL --cap-add NET_BIND_SERVICE", privileged)
        self.assertNotIn("--user 0:0", script)

    def test_refuses_foreign_container(self):
        self.add()
        with mock.patch.object(peers, "api", return_value=(200, b'{"Config":{"Labels":{}}}')):
            with self.assertRaises(peers.PeerError):
                peers._owned(self.root, "ol-peer-fwd-123456abcdef")

    def test_deleting_peer_in_use_preserves_metadata(self):
        peer_id = self.add()
        forward.save(self.root, {"rules": [self.rule(peer_id)]})
        with self.assertRaises(peers.PeerError):
            peers.delete_server(self.root, peer_id)
        self.assertEqual(peers.get(self.root, peer_id)["name"], self.form["name"])

    def test_provisioning_ready_transport_never_restarts_it(self):
        peer_id = self.add()
        peers._profile(self.root, peer_id, "reality", self.profile())
        with mock.patch.object(peers, "test_server"), mock.patch.object(peers, "run_ssh") as run:
            peers.provision(self.root, peer_id, "reality", "24444", "changed.example")
        run.assert_not_called()
        self.assertEqual(peers.get(self.root, peer_id)["transports"]["reality"]["port"], 24443)

    def test_add_forward_rolls_back_without_legacy_restart(self):
        peer_id = self.add()
        form = self.rule(peer_id) | {"transport": "reality"}
        with mock.patch.object(ops, "ROOT", self.root), mock.patch.object(peers, "create_forward"), \
             mock.patch.object(peers, "delete_forward") as delete, \
             mock.patch.object(ops, "apply_forwarding", return_value=(False, "failed")), \
             mock.patch.object(ops, "container_action") as legacy, \
             mock.patch.object(forward, "conflicting_ports", return_value=[]):
            with self.assertRaises(peers.PeerError):
                ops.add_peer_forward(form)
            delete.assert_called_once()
            legacy.assert_not_called()
        self.assertEqual(forward.load(self.root)["rules"], [])

    def test_new_peer_forward_cannot_overlap_legacy_ranges_or_udp_hop(self):
        peer_id = self.add()
        for existing, proto in (({"rules": [{"proto": "tcp", "listen": "2000-2100", "target": "10.0.0.1:80"}]}, "tcp"),
                                ({"rules": [], "hop": {"enabled": True, "proto": "udp", "range": "2000-2100"}}, "both")):
            forward.save(self.root, existing)
            with self.subTest(proto=proto), mock.patch.object(ops, "ROOT", self.root), \
                 mock.patch.object(peers, "create_forward") as create:
                with self.assertRaises(peers.PeerError):
                    ops.add_peer_forward(self.rule(peer_id, proto=proto, listen="2099"))
                create.assert_not_called()
            self.assertEqual(forward.load(self.root)["rules"], existing["rules"])

    def test_running_without_listener_is_rejected_and_cleaned_up(self):
        peer_id = self.add()
        peers._profile(self.root, peer_id, "reality", self.profile())
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path))
            if path.endswith("/json"):
                if len([c for c in calls if c == (method, path)]) == 1:
                    return 404, b""
                return 200, b'{"State":{"Running":true}}'
            return (201, b"{}") if "/create" in path else (204, b"")
        with mock.patch.object(peers, "api", side_effect=api), \
             mock.patch.object(peers, "_host_path", return_value="/private/test"), \
             mock.patch.object(peers.os, "chown", create=True), \
             mock.patch.object(peers.time, "sleep"), \
             mock.patch.object(peers.socket, "create_connection", side_effect=OSError("no listener")):
            # Ownership checks are a separate gate; focus this test on readiness.
            with mock.patch.object(peers, "_owned", side_effect=[None] + [{"State": {"Running": True}}] * 30):
                with self.assertRaises(peers.PeerError):
                    peers.create_forward(self.root, self.rule(peer_id))
        self.assertTrue(any(method == "DELETE" and "ol-peer-fwd-" in path for method, path in calls))
        self.assertFalse(any("ol-tunnel" in path or "ol-gateway" in path for method, path in calls))

    def test_peer_page_escapes_names_and_never_renders_credentials(self):
        self.add()
        page = ui.peer_servers({"lang": "fa", "csrf": "test-csrf", "peers": peers.public(self.root)})
        self.assertIn("Peer &lt;A&gt;", page)
        self.assertNotIn("not-in-output", page)
        self.assertIn('type="password"', page)
        self.assertIn("test-csrf", page)


class PeerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(ops, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ops, "container_state", return_value={"state": "running"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.token = app.new_session()
        self.cookie = "ol_console=" + self.token

    def test_unauthenticated_page_redirects_to_login(self):
        response = urllib.request.urlopen(self.base + "/peers")
        self.assertTrue(response.url.endswith("/login"))

    def test_csrf_is_required_before_saving_credentials(self):
        request = urllib.request.Request(self.base + "/peers/add", data=b"host=203.0.113.12",
                                         headers={"Cookie": self.cookie})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code, 400)
        self.assertFalse((self.root / "data/peers/servers.json").exists())

    def test_save_and_render_never_echo_password(self):
        form = {"_csrf": app.csrf_for(self.token), "name": "HTTP peer", "host": "203.0.113.12",
                "password": "NEVER-ECHO-THIS", "username": "root"}
        request = urllib.request.Request(self.base + "/peers/add", data=urllib.parse.urlencode(form).encode(),
                                         headers={"Cookie": self.cookie})
        page = urllib.request.urlopen(request).read().decode()
        self.assertIn("HTTP peer", page)
        self.assertNotIn("NEVER-ECHO-THIS", page)
        self.assertEqual(len(peers.public(self.root)), 1)

    def test_remote_authenticated_handler_can_save_server_without_https(self):
        handler = object.__new__(app.Handler)
        handler.connection = object()
        handler.client_address = ("203.0.113.20", 12345)
        handler._tr = lambda key: key
        with mock.patch.object(peers, "save_server") as save:
            result = handler._peers_add({"password": "private"})
        save.assert_called_once_with(self.root, {"password": "private"})
        self.assertEqual(result[0], "/peers")
        self.assertEqual(result[2], "ok")


if __name__ == "__main__":
    unittest.main()
