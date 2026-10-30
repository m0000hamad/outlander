"""Real SOCKS/TLS regression: client SNI must not replace a forward's target."""
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

from outlander import probe, render


class GatewayDestinationTests(unittest.TestCase):
    def test_sniffing_is_for_routing_only(self):
        config = render.gateway_config({'channels': [], 'settings': {}, 'secret': 'test'})
        inbound = next(i for i in config['inbounds'] if i['protocol'] == 'socks')
        self.assertTrue(inbound['sniffing']['enabled'])
        self.assertTrue(inbound['sniffing']['routeOnly'])

    @unittest.skipUnless(os.environ.get('XRAY_BIN') or shutil.which('xray'), 'Xray binary needed for traffic regression')
    def test_real_tls_sni_does_not_redirect_to_the_listener_address(self):
        executable = os.environ.get('XRAY_BIN') or shutil.which('xray')
        # Model a forwarding destination distinct from the address its SNI resolves to.
        with socket.socket() as intended, socket.socket() as sni_target, socket.socket() as ephemeral:
            intended.bind(('127.0.0.2', 0))
            target_port = intended.getsockname()[1]
            sni_target.bind(('127.0.0.1', target_port))
            intended.listen()
            sni_target.listen()
            intended.settimeout(8)
            sni_target.settimeout(8)
            ephemeral.bind(('127.0.0.1', 0))
            proxy_port = ephemeral.getsockname()[1]
            ephemeral.close()
            def serve(listener, answer):
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(5)
                        connection.recv(8192)
                        connection.sendall(answer)
                except OSError:
                    pass
            for listener, answer in ((intended, b'INTENDED'), (sni_target, b'SNI_REDIRECT')):
                threading.Thread(target=serve, args=(listener, answer), daemon=True).start()
            config = render.gateway_config({'channels': [], 'settings': {'direct_domestic': False}, 'secret': 'test'})
            inbound = next(i for i in config['inbounds'] if i['protocol'] == 'socks')
            inbound.update(listen='127.0.0.1', port=proxy_port)
            config['inbounds'] = [inbound]
            config['dns'].setdefault('hosts', {})['loop.test'] = '127.0.0.1'
            for outbound in config['outbounds']:
                if outbound['protocol'] == 'freedom':
                    outbound.setdefault('settings', {})['finalRules'] = [{'action': 'allow', 'ip': ['127.0.0.0/8']}]
            output_bio = ssl.MemoryBIO()
            client = ssl.create_default_context().wrap_bio(ssl.MemoryBIO(), output_bio, server_hostname='loop.test')
            try:
                client.do_handshake()
            except ssl.SSLWantReadError:
                pass
            hello = output_bio.read()
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'config.json'
                path.write_text(json.dumps(config))
                process = subprocess.Popen([executable, 'run', '-c', str(path)], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    for _ in range(50):
                        if process.poll() is not None:
                            self.fail('Xray exited before listening')
                        try:
                            with socket.create_connection(('127.0.0.1', proxy_port), .1):
                                break
                        except OSError:
                            time.sleep(.1)
                    with probe._socks_connect('127.0.0.1', proxy_port, '127.0.0.2', target_port, 5) as connection:
                        connection.sendall(hello)
                        self.assertEqual(connection.recv(64), b'INTENDED')
                finally:
                    process.terminate()
                    process.wait(timeout=5)
