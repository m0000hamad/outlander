import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import openvpn, openvpn_server, openvpn_runtime, peers, ui
from outlander import forward


PROFILE = '''client
dev tun
proto udp
remote vpn.example.com 1194
<ca>
PRIVATE-CA
</ca>
<cert>
PRIVATE-CERT
</cert>
<key>
PRIVATE-KEY
</key>
'''


class OpenVPNTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def imported(self):
        return openvpn.import_profile(self.root, {"name": "VPN <test>", "ovpn": PROFILE})

    def rule(self, peer_id):
        return forward.normalise_rule({"mode": "peer", "transport": "openvpn", "peer_id": peer_id,
            "peer_rule_id": "a" * 12, "listen": "12345", "target": "10.8.0.1:443", "proto": "both"})

    def test_import_authenticates_server_and_discards_host_routes(self):
        clean = openvpn.sanitise(PROFILE + "redirect-gateway def1\nroute 0.0.0.0 0.0.0.0\n")
        self.assertIn("remote-cert-tls server", clean)
        self.assertIn("route-nopull", clean)
        self.assertNotIn("redirect-gateway", clean)
        self.assertNotIn("route 0.0.0.0", clean)

    def test_rejects_executable_and_external_directives(self):
        for directive in ("up /tmp/evil", "plugin /tmp/evil", "config /tmp/other", "management 0.0.0.0 5555",
                          "log /repo/secret", "ca /repo/secret", "script-security 3", "remote -bad 1194",
                          "remote vpn.example.com 70000", "auth-user-pass /etc/shadow", "<connection>"):
            with self.subTest(directive=directive), self.assertRaises(peers.PeerError):
                openvpn.sanitise(PROFILE + directive)

    def test_password_profile_credentials_are_required_and_private(self):
        profile = "client\nremote example.com 1194\nauth-user-pass\n<ca>CA</ca>\n"
        with self.assertRaises(peers.PeerError):
            openvpn.sanitise(profile)
        peer_id = openvpn.import_profile(self.root, {"name": "Password VPN", "ovpn": profile,
                                                   "vpn_username": "private-user", "vpn_password": "private-password"})
        saved = peers.get(self.root, peer_id)["transports"]["openvpn"]
        self.assertIn("auth-user-pass /vpn/auth.txt", saved["config"])
        public = json.dumps(peers.public(self.root))
        for secret in ("private-user", "private-password", "<ca>"):
            self.assertNotIn(secret, public)

    def test_import_is_selectable_but_has_no_remote_operations(self):
        peer_id = self.imported()
        context = {"lang": "fa", "csrf": "csrf-test", "peers": peers.public(self.root)}
        page = ui.peer_servers(context)
        self.assertIn("VPN &lt;test&gt;", page)
        self.assertNotIn('action="/peers/test"', page)
        self.assertNotIn('action="/peers/provision"', page)
        self.assertNotIn("PRIVATE-KEY", page)
        config = peers.forward_config(self.rule(peer_id), peers.get(self.root, peer_id))
        self.assertEqual(config["outbounds"][0]["streamSettings"]["sockopt"]["mark"], 4242)
        self.assertEqual(config["inbounds"][0]["settings"]["network"], "tcp,udp")

    def test_forward_uses_private_namespace_without_repository_or_docker_socket(self):
        peer_id = self.imported()
        rule = self.rule(peer_id)
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/containers/ol-console/json":
                return 200, b'{"Image":"sha256:console"}'
            if path.endswith("/exec"):
                return 201, b'{"Id":"exec-id"}'
            if path == "/exec/exec-id/json":
                return 200, b'{"ExitCode":0}'
            return (201 if "/create?" in path else 204), b""
        with mock.patch.object(peers, "api", side_effect=api), mock.patch.object(peers, "_host_path", side_effect=lambda r, p: "/host/" + p.name), mock.patch.object(peers, "_owned", side_effect=[None, {"State": {"Running": True}}]):
            peers.create_forward(self.root, rule)
        payload = next(c[2] for c in calls if "/create?" in c[1])
        host = payload["HostConfig"]
        self.assertEqual(host["NetworkMode"], "bridge")
        self.assertEqual(set(host["PortBindings"]), {"12345/tcp", "12345/udp"})
        self.assertEqual(payload["Image"], "sha256:console")
        self.assertNotIn("/repo:", json.dumps(host["Binds"]))
        self.assertNotIn("docker.sock", json.dumps(payload))
        self.assertTrue(all(bind.endswith(":ro") for bind in host["Binds"]))

    def test_failed_start_removes_container_and_never_changes_global_routes(self):
        peer_id = self.imported()
        def api(method, path, payload=None):
            if path == "/containers/ol-console/json":
                return 200, b'{"Image":"image"}'
            return (201 if "/create?" in path else 500), b""
        with mock.patch.object(peers, "api", side_effect=api) as docker, mock.patch.object(peers, "_host_path", return_value="/private"), mock.patch.object(peers, "_owned", return_value=None):
            with self.assertRaises(peers.PeerError):
                peers.create_forward(self.root, self.rule(peer_id))
        self.assertTrue(any(c.args[0] == "DELETE" for c in docker.call_args_list))

    def test_import_delete_is_blocked_while_forward_exists(self):
        peer_id = self.imported()
        forward.save(self.root, {"rules": [self.rule(peer_id)]})
        with self.assertRaises(peers.PeerError):
            peers.delete_server(self.root, peer_id)
        forward.save(self.root, {"rules": []})
        peers.delete_server(self.root, peer_id)
        self.assertEqual(peers.public(self.root), [])

    def test_remote_setup_does_not_send_client_private_key_or_change_default_route(self):
        certs = {name: name + "-SECRET" for name in ("ca.crt", "server.crt", "server.key", "client.crt", "client.key", "tls.key")}
        profile = {"port": 1194, "subnet": "10.80.90.0/24", "credentials": certs}
        script = openvpn_server.provision_script("a" * 12, "b" * 12, profile)
        self.assertNotIn("client.key", script)
        self.assertIn("openvpn-server@ol-", script)
        self.assertIn("OUTLANDER_ERROR:port_busy", script)
        self.assertNotIn("redirect-gateway", script)
        client = openvpn_server.client_config({"host": "203.0.113.1"}, profile)
        self.assertIn("verify-x509-name outlander-server name", client)
        self.assertNotIn("server.key-SECRET", client)

    def test_runtime_installs_fail_closed_rules_before_starting_vpn(self):
        events = []
        def run(args, **kwargs):
            events.append(args)
            return mock.Mock(stdout="")
        failed = mock.Mock()
        failed.poll.return_value = 1
        with mock.patch.object(openvpn_runtime.signal, "signal"), mock.patch.object(openvpn_runtime.Path, "unlink"), mock.patch.object(openvpn_runtime.subprocess, "run", side_effect=run), mock.patch.object(openvpn_runtime.subprocess, "Popen", side_effect=lambda args, **kw: events.append(args) or failed):
            with self.assertRaisesRegex(RuntimeError, "OpenVPN failed"):
                openvpn_runtime.main()
        vpn_index = next(i for i, args in enumerate(events) if args[0] == "openvpn")
        self.assertTrue(any("prohibit" in args for args in events[:vpn_index]))
        self.assertFalse(any(args[0] == "xray" for args in events))

    def test_ready_waits_for_the_same_docker_exec_to_finish(self):
        inspections = iter([b'{"Running":true,"ExitCode":null}', b'{"Running":false,"ExitCode":0}'])
        def api(method, path, payload=None):
            if path.endswith("/exec"):
                return 201, b'{"Id":"readiness-check"}'
            if path.endswith("/start"):
                return 200, b""
            return 200, next(inspections)
        with mock.patch.object(peers, "api", side_effect=api) as docker, mock.patch.object(peers, "_owned", return_value={"State": {"Running": True}}), mock.patch.object(openvpn.time, "sleep"):
            openvpn.wait_ready(self.root, "vpn-container", "/tmp/openvpn-ready", 2)
        self.assertEqual(sum(call.args[1].endswith("/exec") for call in docker.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
