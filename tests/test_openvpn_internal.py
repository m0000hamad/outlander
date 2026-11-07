import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from console import openvpn_internal as internal, openvpn, openvpn_server, peers, ops, ui, app
from outlander import forward


class InternalOpenVPNTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.certs = {name: name + "-PRIVATE" for name in
                      ("ca.crt", "server.crt", "server.key", "client.crt", "client.key", "tls.key")}

    def entry(self, status="ready"):
        peer_id = internal.ensure_peer(self.root)
        peers._profile(self.root, peer_id, internal.KIND, {"status": status, "enabled": True,
            "credentials": self.certs, "subnet": "10.90.91.0/24", "network": internal.names(self.root)[0],
            "config": "PRIVATE-CLIENT-CONFIG"})
        return peer_id

    def test_entry_is_singleton_and_has_no_ssh_credentials(self):
        first = internal.ensure_peer(self.root)
        self.assertEqual(first, internal.ensure_peer(self.root))
        peer = peers.get(self.root, first)
        self.assertTrue(peer["internal"])
        self.assertNotIn("password", peer)
        with mock.patch.object(peers, "run_ssh") as ssh:
            with self.assertRaises(peers.PeerError):
                peers.test_server(self.root, first)
            with self.assertRaises(peers.PeerError):
                peers.provision(self.root, first, internal.KIND)
            ssh.assert_not_called()

    def test_status_requires_running_owned_service(self):
        self.entry()
        with mock.patch.object(peers, "_owned", return_value=None):
            self.assertEqual(internal.status(self.root)["state"], "stopped")
            with self.assertRaises(peers.PeerError):
                internal.forward_form(self.root, {"mode": internal.KIND})
        with mock.patch.object(peers, "_owned", return_value={"State": {"Running": True}}):
            form = internal.forward_form(self.root, {"mode": internal.KIND, "listen": "12345"})
        self.assertEqual(form["mode"], "peer")
        self.assertEqual(form["transport"], internal.KIND)
        self.assertEqual(form["listen"], "12345")

    def test_existing_service_cannot_be_disabled_with_active_forward(self):
        peer_id = self.entry()
        forward.save(self.root, {"rules": [{"peer_id": peer_id}]})
        with mock.patch.object(peers, "api") as api:
            with self.assertRaises(peers.PeerError):
                internal.disable(self.root, peer_id)
            api.assert_not_called()
        self.assertEqual(peers.get(self.root, peer_id)["transports"][internal.KIND]["status"], "ready")

    def test_disable_removes_only_owned_container_retains_retry_credentials(self):
        peer_id = self.entry()
        with mock.patch.object(peers, "_owned", return_value={"State": {"Running": True}}), mock.patch.object(peers, "api", return_value=(204, b"")) as api:
            internal.disable(self.root, peer_id)
        api.assert_called_once_with("DELETE", f"/containers/{internal.names(self.root)[1]}?force=1")
        profile = peers.get(self.root, peer_id)["transports"][internal.KIND]
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["credentials"], self.certs)
        self.assertEqual(internal.status(self.root)["state"], "disabled")

    def test_cannot_delete_local_entry_through_remote_server_actions(self):
        peer_id = self.entry()
        with self.assertRaises(peers.PeerError):
            peers.delete_server(self.root, peer_id)
        with self.assertRaises(peers.PeerError):
            peers.remove_transport(self.root, peer_id, internal.KIND)

    def test_private_network_ownership_is_checked(self):
        with mock.patch.object(peers, "api", return_value=(200, b'{"Labels":{"outlander.peer.instance":"foreign"}}')):
            with self.assertRaises(peers.PeerError):
                internal._ensure_network(self.root, "network")

    def test_failed_certificate_creation_retains_enable_intent_for_retry(self):
        peer_id = internal.ensure_peer(self.root)
        with mock.patch.object(openvpn_server, "credentials", side_effect=RuntimeError("key generation failed")):
            with self.assertRaises(RuntimeError):
                internal.provision(self.root, peer_id)
        self.assertTrue(peers.get(self.root, peer_id)["transports"][internal.KIND]["enabled"])

    def test_interrupted_vpn_probe_removes_its_credentials_only(self):
        container_id = "c" * 64
        rule_id = "a" * 12
        folder = peers.directory(self.root) / "forwards" / rule_id
        folder.mkdir(parents=True)
        for filename in ("config.json", "client.ovpn", "auth.txt", "keep.txt"):
            peers._write(folder / filename, "private")
        def api(method, path, payload=None):
            return (204, b"") if method == "DELETE" else (200, json.dumps([{"Id": container_id}]).encode())
        info = {"Config": {"Labels": {"outlander.peer.probe": "true", "outlander.peer.probe_rule_id": rule_id}}}
        with mock.patch.object(peers, "api", side_effect=api), mock.patch.object(peers, "_owned", return_value=info):
            peers.cleanup_probes(self.root)
        self.assertEqual([p.name for p in folder.iterdir()], ["keep.txt"])

    def test_failed_data_check_never_reports_ready_and_cleans_resources(self):
        self._provision(False)

    def test_ready_follows_actual_tunnel_data_check_and_server_is_private(self):
        calls = self._provision(True)
        payload = next(payload for method, path, payload in calls if method == "POST" and "/containers/create?" in path)
        self.assertNotEqual(payload["HostConfig"]["NetworkMode"], "host")
        self.assertNotIn("PortBindings", payload["HostConfig"])
        self.assertNotIn("docker.sock", json.dumps(payload))
        self.assertNotIn("/repo:", json.dumps(payload["HostConfig"]["Binds"]))
        self.assertEqual(payload["HostConfig"]["Sysctls"]["net.ipv4.ip_forward"], "1")

    def _provision(self, traffic_ok):
        peer_id = self.entry()
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if path.startswith("/networks/") and method == "GET":
                return 404, b""
            if path == "/containers/ol-console/json":
                return 200, b'{"Image":"console-image"}'
            return (201 if path == "/networks/create" or "create?" in path else 204), b""
        def data_check(*args):
            profile = peers.get(self.root, peer_id)["transports"][internal.KIND]
            self.assertEqual(profile["status"], "installing")
            return traffic_ok
        with mock.patch.object(peers, "api", side_effect=api), mock.patch.object(peers, "_owned", return_value=None), mock.patch.object(peers, "_host_path", side_effect=lambda r, p: "/host/" + p.name), mock.patch.object(openvpn, "wait_ready"), mock.patch.object(openvpn, "create_forward") as create, mock.patch.object(peers, "delete_forward") as delete, mock.patch.object(internal.probe, "socks5_http_reachable", side_effect=data_check):
            if traffic_ok:
                internal.provision(self.root, peer_id)
            else:
                with self.assertRaises(peers.PeerError):
                    internal.provision(self.root, peer_id)
            create.assert_called_once()
            delete.assert_called_once()
        profile = peers.get(self.root, peer_id)["transports"][internal.KIND]
        self.assertEqual(profile["status"], "ready" if traffic_ok else "installing")
        self.assertEqual(profile["credentials"], self.certs)
        if not traffic_ok:
            self.assertTrue(any(method == "DELETE" for method, _, _ in calls))
        public = json.dumps(peers.public(self.root))
        self.assertNotIn("-PRIVATE", public)
        return calls

    def test_local_toggle_and_forward_label_are_explicit(self):
        peer_id = self.entry()
        ctx = {"lang": "fa", "csrf": "csrf-value", "peers": peers.public(self.root),
               "internal_openvpn": {"state": "ready", "enabled": True}}
        page = ui.peer_servers(ctx)
        self.assertIn('action="/peers/openvpn-internal"', page)
        self.assertIn('name="enabled" value="1" checked', page)
        self.assertIn("OpenVPN داخلی", page)
        self.assertNotIn('action="/peers/provision"', page)
        self.assertNotIn("server.key-PRIVATE", page)
        self.assertNotIn("PRIVATE-CLIENT-CONFIG", page)
        rule = forward.normalise_rule({"mode": "peer", "transport": internal.KIND, "peer_id": peer_id,
            "peer_rule_id": "a"*12, "listen": "12345", "proto": "both", "target": "10.90.91.1:80"})
        self.assertIn("اینترنال", ui._mode_label(rule, "fa"))
        self.assertEqual(ui._mode_label(rule, "en"), "Internal OpenVPN")

    def test_edit_internal_mode_maps_to_existing_peer_representation(self):
        peer_id = self.entry()
        old = forward.normalise_rule({"mode": "peer", "transport": internal.KIND, "peer_id": peer_id,
            "peer_rule_id": "a"*12, "listen": "12345", "proto": "tcp", "target": "10.90.91.1:80"})
        form = {"mode": internal.KIND, "listen": "12346", "proto": "udp", "target": "10.90.91.1:81"}
        with mock.patch.object(ops, "ROOT", self.root), mock.patch.object(peers, "_owned", return_value={"State": {"Running": True}}):
            edited = ops._edited_rule(old, form)
        self.assertEqual(edited["transport"], internal.KIND)
        self.assertEqual(edited["mode"], "peer")
        self.assertEqual(edited["peer_rule_id"], old["peer_rule_id"])


if __name__ == "__main__":
    unittest.main()
