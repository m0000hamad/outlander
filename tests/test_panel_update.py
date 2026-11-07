import json
import unittest
from unittest import mock

from console import ops


class SplitImageTests(unittest.TestCase):
    def test_plain_reference(self):
        self.assertEqual(ops.split_image("ghcr.io/mhsanaei/3x-ui:latest"),
                         ("ghcr.io/mhsanaei/3x-ui", "latest"))

    def test_missing_tag_defaults_to_latest(self):
        self.assertEqual(ops.split_image("ghcr.io/mhsanaei/3x-ui"),
                         ("ghcr.io/mhsanaei/3x-ui", "latest"))

    def test_registry_port_is_not_a_tag(self):
        self.assertEqual(ops.split_image("registry.local:5000/team/panel"),
                         ("registry.local:5000/team/panel", "latest"))
        self.assertEqual(ops.split_image("registry.local:5000/team/panel:2.1"),
                         ("registry.local:5000/team/panel", "2.1"))

    def test_digest_reference(self):
        reference = "ghcr.io/mhsanaei/3x-ui@sha256:abc"
        self.assertEqual(ops.split_image(reference), (reference, ""))


class ClonePayloadTests(unittest.TestCase):
    def inspect_payload(self):
        return {
            "Config": {
                "Image": "ghcr.io/mhsanaei/3x-ui:latest",
                "Env": ["A=1"],
                "Labels": {"com.docker.compose.project": "outlander"},
                "Cmd": None,
                "ArgsEscaped": True,
                "OnBuild": None,
            },
            "HostConfig": {
                "NetworkMode": "host",
                "Binds": ["/root/outlander/data/x-ui:/etc/x-ui"],
                "CapAdd": ["NET_ADMIN", "NET_RAW"],
                "RestartPolicy": {"Name": "unless-stopped"},
                "ContainerID": "abc",
            },
            "State": {"Status": "running"},
        }

    def test_keeps_runtime_configuration(self):
        payload = ops.clone_payload(self.inspect_payload())
        self.assertEqual(payload["Image"], "ghcr.io/mhsanaei/3x-ui:latest")
        self.assertEqual(payload["Env"], ["A=1"])
        self.assertEqual(payload["HostConfig"]["NetworkMode"], "host")
        self.assertEqual(payload["HostConfig"]["Binds"], ["/root/outlander/data/x-ui:/etc/x-ui"])
        self.assertEqual(payload["HostConfig"]["CapAdd"], ["NET_ADMIN", "NET_RAW"])

    def test_drops_inspect_only_fields(self):
        payload = ops.clone_payload(self.inspect_payload())
        self.assertNotIn("ContainerID", payload["HostConfig"])
        self.assertNotIn("ArgsEscaped", payload)
        self.assertNotIn("OnBuild", payload)
        self.assertNotIn("State", payload)

    def test_copy_does_not_alias_inspected_state(self):
        info = self.inspect_payload()
        payload = ops.clone_payload(info)
        payload["HostConfig"]["Binds"].append("/tmp:/tmp")
        payload["Env"].append("B=2")
        self.assertEqual(info["HostConfig"]["Binds"], ["/root/outlander/data/x-ui:/etc/x-ui"])
        self.assertEqual(info["Config"]["Env"], ["A=1"])


class UpdatePanelTests(unittest.TestCase):
    def test_missing_container(self):
        with mock.patch.object(ops, "_docker", return_value=(404, b"")):
            self.assertEqual(ops.update_panel(), ("missing", ""))

    def test_reports_docker_failure(self):
        with mock.patch.object(ops, "_docker", side_effect=ops.DockerUnavailable("no socket")):
            self.assertEqual(ops.update_panel(), ("failed", "no socket"))

    def test_already_current_reapplies_route(self):
        info = {
            "Config": {"Image": "ghcr.io/mhsanaei/3x-ui:latest"},
            "Image": "sha256:same",
            "State": {"Status": "running"},
        }

        def fake(method, path, payload=None, timeout=25):
            if path == "/containers/ol-panel/json":
                return 200, json.dumps(info).encode()
            raise AssertionError(f"unexpected docker call {method} {path}")

        with mock.patch.object(ops, "_docker", side_effect=fake), \
                mock.patch.object(ops, "_pull_image", return_value=None), \
                mock.patch.object(ops, "_image_id", return_value="sha256:same"), \
                mock.patch.object(ops, "patch_panel_route", return_value=(True, "ol-gateway")):
            self.assertEqual(ops.update_panel(), ("current", ""))

    def test_current_panel_warns_when_route_fails(self):
        info = {
            "Config": {"Image": "ghcr.io/mhsanaei/3x-ui:latest"},
            "Image": "sha256:same",
            "State": {"Status": "running"},
        }

        def fake(method, path, payload=None, timeout=25):
            if path == "/containers/ol-panel/json":
                return 200, json.dumps(info).encode()
            raise AssertionError(f"unexpected docker call {method} {path}")

        with mock.patch.object(ops, "_docker", side_effect=fake), \
                mock.patch.object(ops, "_pull_image", return_value=None), \
                mock.patch.object(ops, "_image_id", return_value="sha256:same"), \
                mock.patch.object(ops, "patch_panel_route", return_value=(False, "login failed")):
            self.assertEqual(ops.update_panel(), ("current", "login failed"))


if __name__ == "__main__":
    unittest.main()
