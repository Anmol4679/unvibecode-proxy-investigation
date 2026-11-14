"""Offline proxy routing proof. Only loopback sockets; TLS is mocked, not disabled on a real service."""
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
import unvibecode.client as module
from unvibecode.client import Client, NoRedirect

HOST = 'api.proxy-test.invalid'
FAILURE = 'Cannot reach API. Check the service and SSH tunnel.'

class LocalProxy(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        def read_request():
            first = self.rfile.readline().decode('ascii').strip()
            while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                pass
            return first
        first = read_request()
        self.server.requests.append(first)
        if first.startswith('CONNECT '):
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            self.wfile.flush()
            self.server.requests.append(read_request())
        body = b'{"ok":true,"fixture":"loopback"}'
        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                         + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        self.wfile.flush()

class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

def run_case(label, variable, url, no_proxy='', allow_local=False):
    with Server(('127.0.0.1', 0), LocalProxy) as server:
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        url = url.replace('{PORT}', str(port))
        env = {k: v for k, v in os.environ.items()
               if not k.lower().endswith('_proxy') and k.upper() != 'REQUEST_METHOD'}
        env.update({variable: f'http://127.0.0.1:{port}', 'NO_PROXY': no_proxy})
        original_connect = socket.create_connection
        rows = []
        attempts = []
        def connect(address, *args, **kwargs):
            host, target_port = address
            attempts.append((host, target_port))
            if host == '127.0.0.1' and target_port == port:
                return original_connect(address, *args, **kwargs)
            if allow_local and host == 'localhost' and target_port == port:
                return original_connect(('127.0.0.1', port), *args, **kwargs)
            raise ConnectionRefusedError('Synthetic direct route disabled; no DNS/TCP attempted')
        def fake_tls(context, sock, *args, **kwargs):
            assert kwargs.get('server_hostname') == HOST
            return sock  # Fixture speaks plaintext after CONNECT. No real TLS is exercised.
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(socket, 'create_connection', connect), patch.object(ssl.SSLContext, 'wrap_socket', fake_tls):
                # With a nonempty environment proxy map, Windows urllib uses its
                # environment bypass rules rather than registry bypass fallback.
                assert urllib.request.getproxies().get(variable[:-6].lower()) == f'http://127.0.0.1:{port}'
                for name in ('urllib', 'Client.request'):
                    server.requests.clear()
                    attempts.clear()
                    try:
                        if name == 'urllib':
                            opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())
                            with opener.open(url + '/health', timeout=3) as response:
                                value = json.load(response)
                        else:
                            value = Client(url=url, token='offline-dummy').request('/health')
                        assert value == {'ok': True, 'fixture': 'loopback'}
                        outcome = 'OK'
                    except Exception as exc:
                        outcome = type(exc).__name__ + ': ' + str(exc)
                    proxy_hits = sum(r.startswith('CONNECT ') or r.startswith('GET http://') for r in server.requests)
                    row = {'transport': name, 'outcome': outcome, 'proxy_hits': proxy_hits,
                           'direct_attempts': sum(h != '127.0.0.1' for h, p in attempts),
                           'requests': list(server.requests)}
                    rows.append(row)
                    print(f'{label} | {name} | {outcome} | proxy_hits={proxy_hits} | direct_attempts={row["direct_attempts"]}')
            return {'label': label, 'variable': variable, 'no_proxy': no_proxy, 'rows': rows}
        finally:
            server.shutdown()
            thread.join(timeout=3)

def guard(event, args):
    if event == 'socket.getaddrinfo' and args[0] != '127.0.0.1':
        raise AssertionError('Non-loopback DNS forbidden')
    if event == 'socket.connect' and args[1][0] != '127.0.0.1':
        raise AssertionError('Non-loopback TCP forbidden')
sys.addaudithook(guard)

def main():
    before = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    print(f'unvibecode={unvibecode.__version__}; python={sys.version.split()[0]}; platform={sys.platform}')
    results = []
    cases = [
        ('HTTPS_PROXY remote', 'HTTPS_PROXY', f'https://{HOST}/analysis', '', False),
        ('HTTP_PROXY local HTTP', 'HTTP_PROXY', 'http://localhost:18081', '', False),
        ('HTTPS NO_PROXY control', 'HTTPS_PROXY', f'https://{HOST}/analysis', HOST, False),
        ('HTTP NO_PROXY local control', 'HTTP_PROXY', 'http://localhost:{PORT}', 'localhost', True),
    ]
    for case in cases:
        result = run_case(*case)
        standard, actual = result['rows']
        if case[0] in {'HTTPS_PROXY remote', 'HTTP_PROXY local HTTP'}:
            assert standard['outcome'] == 'OK' and standard['proxy_hits'] == 1
            assert actual['outcome'] == 'RuntimeError: ' + FAILURE and actual['proxy_hits'] == 0
            assert actual['direct_attempts'] == 1
        elif case[0] == 'HTTPS NO_PROXY control':
            assert standard['proxy_hits'] == actual['proxy_hits'] == 0
            assert standard['outcome'].startswith('URLError:')
            assert actual['outcome'] == 'RuntimeError: ' + FAILURE
        else:
            assert standard['outcome'] == actual['outcome'] == 'OK'
            assert standard['proxy_hits'] == actual['proxy_hits'] == 0
        results.append(result)
    try:
        Client(url='http://' + HOST, token='offline-dummy')
    except ValueError:
        print('non-loopback HTTP URL: rejected as designed')
    else:
        raise AssertionError('Expected URL validation')
    assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == before
    print('PASS: request-path assertions; loopback-only sockets; package unchanged')
    return results

if __name__ == '__main__':
    main()
