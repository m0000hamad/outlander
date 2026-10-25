import socket
import threading
import unittest

from outlander import probe


def listening_socket() -> tuple[socket.socket, int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server, server.getsockname()[1]


class TcpProbeTests(unittest.TestCase):
    def test_open_port(self):
        server, port = listening_socket()
        try:
            self.assertTrue(probe.tcp_reachable("127.0.0.1", port, 2))
        finally:
            server.close()

    def test_closed_port(self):
        server, port = listening_socket()
        server.close()
        self.assertFalse(probe.tcp_reachable("127.0.0.1", port, 1))


class Socks5ProbeTests(unittest.TestCase):
    def start(self, reply: bytes) -> int:
        server, port = listening_socket()

        def worker() -> None:
            try:
                connection, _ = server.accept()
                with connection:
                    connection.recv(3)
                    connection.sendall(b"\x05\x00")
                    connection.recv(32)
                    connection.sendall(reply)
            finally:
                server.close()

        threading.Thread(target=worker, daemon=True).start()
        return port

    def test_accepts(self):
        port = self.start(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
        self.assertTrue(probe.socks5_reachable("127.0.0.1", port, "127.0.0.1", 443, 3))

    def test_rejects(self):
        port = self.start(b"\x05\x05\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
        self.assertFalse(probe.socks5_reachable("127.0.0.1", port, "127.0.0.1", 443, 3))

    def test_no_proxy(self):
        server, port = listening_socket()
        server.close()
        self.assertFalse(probe.socks5_reachable("127.0.0.1", port, "127.0.0.1", 443, 1))


if __name__ == "__main__":
    unittest.main()
