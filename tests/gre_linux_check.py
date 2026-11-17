"""Run manually as root in an isolated Linux network namespace.

  sudo unshare --net python3 tests/gre_linux_check.py

Creates only namespace-local links; requires iproute2 and GRE kernel support.
"""
import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outlander import gre


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


remote = subprocess.Popen(['unshare', '--net', 'sleep', '60'])
try:
    # Wait until unshare has installed the separate network namespace.
    import time
    for _ in range(100):
        if Path(f'/proc/{remote.pid}/ns/net').readlink() != Path('/proc/self/ns/net').readlink():
            break
        time.sleep(.02)
    else:
        raise RuntimeError('remote namespace did not start')
    ns = ['nsenter', '-t', str(remote.pid), '-n']
    run('ip', 'link', 'add', 'test-a', 'type', 'veth', 'peer', 'name', 'test-b')
    run('ip', 'link', 'set', 'test-b', 'netns', str(remote.pid))
    run('ip', 'addr', 'add', '10.200.0.1/30', 'dev', 'test-a')
    run('ip', 'link', 'set', 'test-a', 'up')
    run(*ns, 'ip', 'addr', 'add', '10.200.0.2/30', 'dev', 'test-b')
    run(*ns, 'ip', 'link', 'set', 'test-b', 'up')
    run(*ns, 'ip', 'link', 'set', 'lo', 'up')
    run(*ns, 'ip', 'tunnel', 'add', 'test-gre', 'mode', 'gre', 'local', '10.200.0.2', 'remote', '10.200.0.1', 'ttl', '64')
    run(*ns, 'ip', 'addr', 'add', '10.201.0.2/32', 'peer', '10.201.0.1/32', 'dev', 'test-gre')
    run(*ns, 'ip', 'link', 'set', 'test-gre', 'mtu', '1400', 'up')
    rule = {'proto': 'both', 'listen': '28443', 'target': '10.201.0.2:28443', 'mode': 'gre',
            'gre_local': '10.200.0.1', 'gre_remote': '10.200.0.2', 'gre_address': '10.201.0.1'}
    data = {'rules': [rule]}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gre.apply(root, data, cleanup=True)
        gre.apply(root, data, cleanup=True)
        name = next(iter(gre.profiles(data)))
        route = json.loads(run('ip', '-j', 'route', 'get', '10.201.0.2'))[0]
        assert route['dev'] == name, route
        for kind in ('tcp', 'udp'):
            code = '''import socket, sys
kind = socket.SOCK_STREAM if sys.argv[1] == 'tcp' else socket.SOCK_DGRAM
s = socket.socket(type=kind); s.settimeout(8); s.bind(('10.201.0.2', 28443))
if kind == socket.SOCK_STREAM: s.listen(1)
print('ready', flush=True)
if kind == socket.SOCK_STREAM:
 c, _ = s.accept(); c.sendall(c.recv(200)); c.close()
else:
 b, a = s.recvfrom(200); s.sendto(b, a)
s.close()
'''
            server = subprocess.Popen([*ns, 'python3', '-u', '-c', code, kind], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                assert server.stdout.readline().strip() == 'ready'
                with socket.socket(type=socket.SOCK_STREAM if kind == 'tcp' else socket.SOCK_DGRAM) as client:
                    client.settimeout(5); client.connect(('10.201.0.2', 28443)); client.send(b'GRE proof')
                    assert client.recv(200) == b'GRE proof'
                server.wait(timeout=10)
                assert server.returncode == 0, server.stderr.read()
            finally:
                if server.poll() is None: server.kill(); server.wait()
        # Editing an inner address on the same endpoints replaces its old address.
        updated = {'rules': [rule | {'gre_address': '10.201.0.3'}]}
        gre.apply(root, updated, cleanup=True)
        addrs = json.loads(run('ip', '-j', 'address', 'show', 'dev', name))[0]['addr_info']
        assert [a['local'] for a in addrs if a['family'] == 'inet'] == ['10.201.0.3'], addrs
        gre.apply(root, data, cleanup=True)
        # Changing outer endpoints must move the inner peer route to the new link.
        run('ip', 'addr', 'add', '10.200.0.5/30', 'dev', 'test-a')
        run(*ns, 'ip', 'addr', 'add', '10.200.0.6/30', 'dev', 'test-b')
        moved = {'rules': [rule | {'gre_local': '10.200.0.5', 'gre_remote': '10.200.0.6'}]}
        gre.apply(root, moved, cleanup=True)
        moved_name = next(iter(gre.profiles(moved)))
        route = json.loads(run('ip', '-j', 'route', 'get', '10.201.0.2'))[0]
        assert route['dev'] == moved_name, route
        gre.apply(root, {'rules': []}, cleanup=True)
        assert moved_name not in {link['ifname'] for link in json.loads(run('ip', '-j', 'link', 'show'))}

        assert name not in {link['ifname'] for link in json.loads(run('ip', '-j', 'link', 'show'))}
    print('GRE Linux proof passed: TCP/UDP echo, reapply, inner-address edit, cleanup')
finally:
    remote.terminate()
    remote.wait(timeout=5)
