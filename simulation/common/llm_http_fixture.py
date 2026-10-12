"""Bounded loopback Chat Completions fixture for explicit synthetic GTK tests."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time


class SyntheticLLMFixture:
    def __init__(self):
        self.status = 200
        self.delay = 0
        self._closed = False
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                raw = self.rfile.read(min(int(self.headers.get('Content-Length', '0')), 16384))
                payload = json.loads(raw)
                valid = (self.path == '/v1/chat/completions'
                         and payload.get('model') == 'fixture-model'
                         and payload.get('max_tokens') == 8
                         and payload.get('messages', [{}])[-1].get('content')
                         == 'Test de connexion technique. Réponds uniquement OK.')
                code = fixture.status if valid else 400
                fixture.requests.append({'status': code, 'synthetic_only': valid,
                                         'sha256': hashlib.sha256(raw).hexdigest(),
                                         'authorization_present': bool(self.headers.get('Authorization'))})
                time.sleep(fixture.delay)
                self.send_response(code); self.send_header('Content-Type', 'application/json')
                self.end_headers()
                body = {'choices': [{'message': {'content': 'OK'}}]} if code == 200 else {'error': 'fixture'}
                try:
                    self.wfile.write(json.dumps(body).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
