import tempfile
import unittest
from pathlib import Path

from outlander import certs


class DomainTests(unittest.TestCase):
    def test_accepts_real_domains(self):
        for value in ("hy2.example.com", "a.b.co", "S2.Dgtshop.IR", "proxy-1.example.org"):
            self.assertTrue(certs.valid_domain(value), value)

    def test_rejects_bad_domains(self):
        for value in ("", "localhost", "example", "-bad.com", "bad-.com", "a..b.com", "http://x.com", "x_y.com"):
            self.assertFalse(certs.valid_domain(value), value)

    def test_normalise(self):
        self.assertEqual(certs.normalise_domain("  S2.Dgtshop.IR. "), "s2.dgtshop.ir")


class PathTests(unittest.TestCase):
    def test_panel_paths(self):
        cert, key = certs.panel_cert_paths("Proxy.Example.com")
        self.assertEqual(cert, "/root/cert/proxy.example.com/fullchain.pem")
        self.assertEqual(key, "/root/cert/proxy.example.com/privkey.pem")

    def test_host_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(certs.host_cert_dir(Path(tmp), "A.example.com"), Path(tmp) / "certs" / "a.example.com")


if __name__ == "__main__":
    unittest.main()
