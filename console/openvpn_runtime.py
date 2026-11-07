"""Run one VPN and Xray forward in a private Docker network namespace."""
import json
from pathlib import Path
import socket
import signal
import subprocess
import time


def main():
    children = []
    ready_file = Path("/tmp/openvpn-ready")
    ready_file.unlink(missing_ok=True)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def ip(*args):
        subprocess.run(["ip", *args], check=True, capture_output=True)
    try:
        # Only marked Xray sockets use this table. A missing VPN route cannot
        # fall through to the container's ordinary Internet connection.
        ip("rule", "add", "fwmark", "4242", "table", "4242", "priority", "100")
        ip("route", "add", "prohibit", "default", "table", "4242", "metric", "32767")
        ip("-6", "rule", "add", "fwmark", "4242", "table", "4242", "priority", "100")
        ip("-6", "route", "add", "prohibit", "default", "table", "4242")
        vpn = subprocess.Popen(["openvpn", "--config", "/vpn/client.ovpn", "--dev", "tun0",
                                "--route-nopull", "--script-security", "1", "--auth-nocache",
                                "--verb", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(vpn)
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            if vpn.poll() is not None:
                raise RuntimeError("OpenVPN failed to start")
            address = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", "tun0"],
                                     capture_output=True, text=True)
            if " inet " in address.stdout:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("OpenVPN connection timed out")
        ip("route", "add", "default", "dev", "tun0", "table", "4242", "metric", "10")
        children.append(subprocess.Popen(["xray", "run", "-c", "/vpn/config.json"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        config = json.loads(Path("/vpn/config.json").read_text())
        inbound = config["inbounds"][0]
        port = inbound["port"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if children[-1].poll() is not None:
                raise RuntimeError("Forward listener failed")
            network = inbound.get("settings", {}).get("network", "tcp")
            if network == "udp":
                ready = any(line.split()[1].endswith(f":{port:04X}")
                            for line in Path("/proc/net/udp").read_text().splitlines()[1:])
                if ready:
                    break
            else:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    pass
            time.sleep(.1)
        else:
            raise RuntimeError("Forward listener timed out")
        ready_file.touch()
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        raise RuntimeError("OpenVPN forward stopped")
    finally:
        ready_file.unlink(missing_ok=True)
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
