"""Exercise the Dockerfile's package retry shell without network or root."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class ConsoleBuildTests(unittest.TestCase):
    def run_install(self, mode):
        dockerfile = (Path(__file__).resolve().parents[1] / "console/Dockerfile").read_text()
        script = dockerfile.split("RUN ", 1)[1].split("    && certbot --version", 1)[0]
        script = script.replace("\\\n", "").strip()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / "debian.sources"
            sources.write_text("URIs: http://deb.debian.org/debian\n"
                               "URIs: http://deb.debian.org/debian-security\n")
            lists = root / "lists"
            lists.mkdir()
            (lists / "stale-index").touch()
            script = script.replace("/etc/apt/sources.list.d/debian.sources", str(sources))
            script = script.replace("/var/lib/apt/lists", str(lists))
            apt = root / "apt-get"
            apt.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$APT_TEST_LOG"
case "$*" in
  *update*) case "$*" in *APT::Update::Error-Mode=any*) ;; *) exit 90 ;; esac ;;
esac
if grep -q 'deb.debian.org' "$APT_TEST_SOURCES"; then
  case "$APT_TEST_MODE:$*" in
    update:*update*|install:*install*|always:*) exit 1 ;;
  esac
else
  test ! -f "$APT_TEST_LISTS/stale-index" || exit 91
  test "$APT_TEST_MODE" != always || exit 1
fi
exit 0
""")
            apt.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                       APT_TEST_LOG=str(root / "calls"), APT_TEST_SOURCES=str(sources),
                       APT_TEST_LISTS=str(lists), APT_TEST_MODE=mode)
            result = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True)
            return result, (root / "calls").read_text().splitlines(), sources.read_text()

    def test_primary_success_keeps_sources(self):
        result, calls, sources = self.run_install("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn("deb.debian.org", sources)
        self.assertNotIn("fallback mirrors", result.stderr)

    def test_update_failure_retries_on_fallback(self):
        self.assert_fallback("update", 3)

    def test_install_failure_retries_on_fallback(self):
        self.assert_fallback("install", 4)

    def assert_fallback(self, mode, count):
        result, calls, sources = self.run_install(mode)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), count)
        self.assertIn("ftp.debian.org/debian", sources)
        self.assertIn("security.debian.org/debian-security", sources)
        self.assertNotIn("deb.debian.org", sources)
        self.assertIn("fallback mirrors", result.stderr)

    def test_fallback_failure_stops_build(self):
        result, calls, _ = self.run_install("always")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
