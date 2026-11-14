"""
Offline reproduction for UnvibeCode proxy-routing behavior.

Exercises real unvibecode.client.Client.request() transport against an
ephemeral loopback proxy and compares against standard urllib.request.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import threading
import urllib.request
from unittest.mock import patch

sys.dont_write_bytecode = True
import unvibecode
import unvibecode.client as client_module
from unvibecode.client import Client, NoRedirect

HOST = "api.proxy-test.invalid"
FAILURE_MSG = "Cannot reach API. Check the service and SSH tunnel."


class LocalProxyHandler(socketserver.StreamRequestHandler):
    """Minimal loopback HTTP proxy capturing CONNECT and GET requests."""

    def handle(self):
        self.request.settimeout(3)

        def read_request_line():
            first_line = self.rfile.readline().decode("ascii").strip()
            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                pass
            return first_line

        first = read_request_line()
        self.server.requests.append(first)

        if first.startswith("CONNECT "):
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            self.server.requests.append(read_request_line())

        body = b'{"ok":true,"fixture":"loopback"}'
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        self.wfile.write(headers + body)
        self.wfile.flush()


class EphemeralServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def run_case(label, proxy_var, target_url, no_proxy="", allow_local=False):
    """Run comparison test case between urllib and unvibecode Client."""
    with EphemeralServer(("127.0.0.1", 0), LocalProxyHandler) as server:
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        proxy_port = server.server_address[1]
        target_url = target_url.replace("{PORT}", str(proxy_port))

        env = {
            k: v
            for k, v in os.environ.items()
            if not k.lower().endswith("_proxy") and k.upper() != "REQUEST_METHOD"
        }
        env.update(
            {proxy_var: f"http://127.0.0.1:{proxy_port}", "NO_PROXY": no_proxy}
        )

        original_connect = socket.create_connection
        attempts = []

        def controlled_connect(address, *args, **kwargs):
            host, port = address
            attempts.append((host, port))
            if host == "127.0.0.1" and port == proxy_port:
                return original_connect(address, *args, **kwargs)
            if allow_local and host == "localhost" and port == proxy_port:
                return original_connect(("127.0.0.1", proxy_port), *args, **kwargs)
            raise ConnectionRefusedError(
                "Synthetic direct route disabled; no DNS/TCP attempted"
            )

        def mock_tls_wrap(context, sock, *args, **kwargs):
            # Loopback fixture exchanges plaintext HTTP after proxy CONNECT
            assert kwargs.get("server_hostname") == HOST
            return sock

        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                socket, "create_connection", controlled_connect
            ), patch.object(ssl.SSLContext, "wrap_socket", mock_tls_wrap):
                assert (
                    urllib.request.getproxies().get(proxy_var[:-6].lower())
                    == f"http://127.0.0.1:{proxy_port}"
                )

                rows = []
                for transport_name in ("urllib", "Client.request"):
                    server.requests.clear()
                    attempts.clear()
                    try:
                        if transport_name == "urllib":
                            opener = urllib.request.build_opener(
                                urllib.request.ProxyHandler(), NoRedirect()
                            )
                            with opener.open(
                                target_url + "/health", timeout=3
                            ) as resp:
                                payload = json.load(resp)
                        else:
                            payload = Client(
                                url=target_url, token="offline-dummy"
                            ).request("/health")

                        assert payload == {"ok": True, "fixture": "loopback"}
                        outcome = "OK"
                    except Exception as exc:
                        outcome = f"{type(exc).__name__}: {exc}"

                    proxy_hits = sum(
                        req.startswith("CONNECT ") or req.startswith("GET http://")
                        for req in server.requests
                    )
                    direct_attempts = sum(h != "127.0.0.1" for h, _ in attempts)

                    row = {
                        "transport": transport_name,
                        "outcome": outcome,
                        "proxy_hits": proxy_hits,
                        "direct_attempts": direct_attempts,
                    }
                    rows.append(row)
                    print(
                        f"{label} | {transport_name} | {outcome} | "
                        f"proxy_hits={proxy_hits} | direct_attempts={direct_attempts}"
                    )

            return rows
        finally:
            server.shutdown()
            thread.join(timeout=3)


# Safety audit hook: guarantee no external socket connection can escape loopback
def audit_hook(event, args):
    if event == "socket.getaddrinfo" and args[0] != "127.0.0.1":
        raise AssertionError(f"Non-loopback DNS forbidden: {args[0]}")
    if event == "socket.connect" and args[1][0] != "127.0.0.1":
        raise AssertionError(f"Non-loopback TCP forbidden: {args[1][0]}")


sys.addaudithook(audit_hook)


def main():
    package_file = Path(client_module.__file__)
    initial_hash = hashlib.sha256(package_file.read_bytes()).hexdigest()

    print(
        f"unvibecode={unvibecode.__version__}; "
        f"python={sys.version.split()[0]}; platform={sys.platform}"
    )

    cases = [
        ("HTTPS_PROXY remote", "HTTPS_PROXY", f"https://{HOST}/analysis", "", False),
        ("HTTP_PROXY local HTTP", "HTTP_PROXY", "http://localhost:18081", "", False),
        ("HTTPS NO_PROXY control", "HTTPS_PROXY", f"https://{HOST}/analysis", HOST, False),
        ("HTTP NO_PROXY local control", "HTTP_PROXY", "http://localhost:{PORT}", "localhost", True),
    ]

    for label, var, url, no_proxy, allow_local in cases:
        standard, actual = run_case(label, var, url, no_proxy, allow_local)

        if label in {"HTTPS_PROXY remote", "HTTP_PROXY local HTTP"}:
            assert standard["outcome"] == "OK" and standard["proxy_hits"] == 1
            assert (
                actual["outcome"] == f"RuntimeError: {FAILURE_MSG}"
                and actual["proxy_hits"] == 0
                and actual["direct_attempts"] == 1
            )
        elif label == "HTTPS NO_PROXY control":
            assert standard["proxy_hits"] == actual["proxy_hits"] == 0
            assert standard["outcome"].startswith("URLError:")
            assert actual["outcome"] == f"RuntimeError: {FAILURE_MSG}"
        else:
            assert standard["outcome"] == actual["outcome"] == "OK"
            assert standard["proxy_hits"] == actual["proxy_hits"] == 0

    # Verify non-loopback HTTP rejection
    try:
        Client(url=f"http://{HOST}", token="offline-dummy")
    except ValueError:
        print("non-loopback HTTP URL: rejected as designed")
    else:
        raise AssertionError("Expected URL validation failure for non-loopback HTTP")

    # Verify installed package was not modified
    final_hash = hashlib.sha256(package_file.read_bytes()).hexdigest()
    assert final_hash == initial_hash, "Package source was modified during execution"

    print("PASS: request-path assertions; loopback-only sockets; package unchanged")


if __name__ == "__main__":
    main()
