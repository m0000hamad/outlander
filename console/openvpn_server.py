"""Provision an independently named OpenVPN TLS server over pinned SSH."""
import base64
import ipaddress
import secrets
import subprocess
import tempfile
from pathlib import Path


def credentials() -> dict:
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        def run(*args):
            subprocess.run(["openssl", *args], cwd=folder, check=True, capture_output=True, timeout=30)
        run("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-keyout", "ca.key", "-out", "ca.crt",
            "-days", "3650", "-subj", "/CN=Outlander OpenVPN CA", "-addext", "basicConstraints=critical,CA:TRUE")
        for name, usage in (("server", "serverAuth"), ("client", "clientAuth")):
            run("req", "-newkey", "rsa:3072", "-nodes", "-keyout", name + ".key", "-out", name + ".csr",
                "-subj", "/CN=outlander-" + name)
            key_usage = "digitalSignature,keyEncipherment" if name == "server" else "digitalSignature"
            (folder / "extensions").write_text("basicConstraints=CA:FALSE\nkeyUsage=" + key_usage + "\nextendedKeyUsage=" + usage + "\n")
            run("x509", "-req", "-in", name + ".csr", "-CA", "ca.crt", "-CAkey", "ca.key",
                "-set_serial", str(secrets.randbits(128)), "-out", name + ".crt", "-days", "825", "-extfile", "extensions")
        result = {name: (folder / name).read_text() for name in ("ca.crt", "server.crt", "server.key", "client.crt", "client.key")}
        # OpenVPN static key format: 2048 bits, 16 lines of hexadecimal.
        key = secrets.token_hex(256)
        result["tls.key"] = "-----BEGIN OpenVPN Static key V1-----\n" + "\n".join(key[i:i+32] for i in range(0, len(key), 32)) + "\n-----END OpenVPN Static key V1-----\n"
        return result


def service_name(instance, peer_id):
    return "ol-" + instance + "-" + peer_id


def client_config(peer, profile):
    c = profile["credentials"]
    return (f"client\ndev tun0\nproto udp4\nremote {peer['host']} {profile['port']}\nnobind\npersist-key\npersist-tun\n"
            "remote-cert-tls server\nverify-x509-name outlander-server name\nroute-nopull\n"
            "tls-version-min 1.2\ndata-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305\n"
            + "".join(f"<{tag}>\n{c[name]}</{tag}>\n" for tag, name in
                      (("ca", "ca.crt"), ("cert", "client.crt"), ("key", "client.key"), ("tls-crypt", "tls.key"))))


def provision_script(instance, peer_id, profile):
    name = service_name(instance, peer_id)
    folder = "/etc/openvpn/server/" + name
    device = "ov" + peer_id
    subnet = profile["subnet"]
    network = str(ipaddress.ip_network(subnet).network_address)
    port = profile["port"]
    # Add only exact rules for this service, and remove only these rules on stop.
    firewall = "#!/bin/sh\nset -eu\n"
    for table, chain, rule in (
        ("filter", "INPUT", f"-p udp --dport {port}"),
        ("filter", "INPUT", f"-i {device}"),
        ("filter", "FORWARD", f"-i {device} -s {subnet}"),
        ("filter", "FORWARD", f"-o {device} -d {subnet} -m conntrack --ctstate ESTABLISHED,RELATED"),
        ("nat", "POSTROUTING", f"-s {subnet} ! -o {device}")):
        target = "MASQUERADE" if table == "nat" else "ACCEPT"
        full = f"-t {table} {{action}} {chain} {rule} -m comment --comment {name} -j {target}"
        check = "iptables " + full.format(action="-C")
        firewall += f'if [ "$script_type" = up ]; then\n {check} 2>/dev/null || iptables {full.format(action="-I")}\nelse\n if {check} 2>/dev/null; then iptables {full.format(action="-D")}; fi\nfi\n'
    config = (f"port {port}\nproto udp4\ndev {device}\ndev-type tun\ntopology subnet\nserver {network} 255.255.255.0\n"
              f"ca {folder}/ca.crt\ncert {folder}/server.crt\nkey {folder}/server.key\ntls-crypt {folder}/tls.key\n"
              "dh none\nremote-cert-tls client\ntls-version-min 1.2\ndata-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305\n"
              f"script-security 2\nup {folder}/firewall.sh\ndown {folder}/firewall.sh\ndown-pre\n"
              "keepalive 10 60\nduplicate-cn\nverb 2\n")
    files = {k: v for k, v in profile["credentials"].items() if k not in {"client.key", "client.crt"}}
    files["firewall.sh"] = firewall
    writes = "\n".join(f"echo {base64.b64encode(content.encode()).decode()} | base64 -d > {folder}/{filename}"
                       for filename, content in files.items())
    encoded = base64.b64encode(config.encode()).decode()
    return f'''set -eu
test "$(id -u)" = 0 || {{ echo OUTLANDER_ERROR:root_required >&2; exit 1; }}
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y openvpn iptables iproute2
umask 077
if [ -f {folder}/owner ]; then
 test "$(cat {folder}/owner)" = {instance} || {{ echo OUTLANDER_ERROR:not_owned >&2; exit 1; }}
 if systemctl is-active --quiet openvpn-server@{name}; then echo OUTLANDER_READY; exit 0; fi
fi
if ss -H -lnu 'sport = :{port}' | grep -q .; then echo OUTLANDER_ERROR:port_busy >&2; exit 1; fi
test ! -e /etc/openvpn/server/{name}.conf || test -f {folder}/owner
if ip -4 route show | grep -F '{network}/24' | grep -v 'dev {device}' | grep -q .; then exit 1; fi
mkdir -p {folder}
echo {instance} > {folder}/owner
{writes}
chmod 700 {folder}/firewall.sh
echo {encoded} | base64 -d > /etc/openvpn/server/{name}.conf
echo net.ipv4.ip_forward=1 > /etc/sysctl.d/60-outlander-openvpn.conf
sysctl -w net.ipv4.ip_forward=1 >/dev/null
systemctl enable --now openvpn-server@{name}
systemctl is-active --quiet openvpn-server@{name}
echo OUTLANDER_READY
'''


def provision(root, peer_id, port):
    from . import peers
    peer = peers.get(root, peer_id)
    remote_port = int(port or 1194)
    if not 1 <= remote_port <= 65535:
        raise peers.PeerError("Invalid OpenVPN port")
    profile = peer.get("transports", {}).get("openvpn", {})
    if not profile.get("credentials"):
        profile = {"port": remote_port, "subnet": f"10.{secrets.randbelow(200)+20}.{secrets.randbelow(254)+1}.0/24",
                   "credentials": credentials()}
    profile.update(status="installing")
    profile["config"] = client_config(peer, profile)
    peers._profile(root, peer_id, "openvpn", profile)
    result = peers.run_ssh(root, peer, provision_script(peers.load(root)["instance"], peer_id, profile), timeout=300)
    if "OUTLANDER_READY" not in result:
        raise peers.PeerError("OpenVPN server installation did not finish")
    profile.update(status="ready", message="OpenVPN UDP ready / آماده")
    peers._profile(root, peer_id, "openvpn", profile)


def remove(root, peer_id):
    from . import peers
    instance = peers.load(root)["instance"]
    name = service_name(instance, peer_id)
    peers.run_ssh(root, peers.get(root, peer_id), f'''set -eu
test "$(cat /etc/openvpn/server/{name}/owner)" = {instance}
systemctl disable --now openvpn-server@{name}
''')
