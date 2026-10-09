#!/usr/bin/env python3
"""Deterministic localhost-only HTTP failure lab for transport.http.

Never proxies an external URL. All payloads are artificial. Redirects are
relative except the explicitly validated loopback-origin test route.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common.fake_books import FakeBookFactory, epub_bytes
from plugin_scenarios import reproducible_epub

SEED = 20261009
LARGE = b"fictional-chunk-" * 400


class TransportFakeApp:
    def __init__(self, cache_dir: Path, *, seed: int = SEED, rebuild: bool = False):
        self.seed = int(seed)
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        self.artifact = cache / "transport-synthetic.epub"
        marker = cache / "seed.txt"
        if rebuild or not self.artifact.is_file() or not marker.is_file() or marker.read_text() != str(seed):
            book = FakeBookFactory(self.seed, "ux-http-transport").make(1)
            self.artifact.write_bytes(reproducible_epub(epub_bytes(book)))
            marker.write_text(str(self.seed), encoding="ascii")

    @staticmethod
    def send(h, status: int, mime: str, body: bytes, *, length: int | None = None,
             include_length: bool = True, extra: dict[str, str] | None = None):
        h.send_response(int(status))
        h.send_header("Content-Type", mime)
        if include_length:
            h.send_header("Content-Length", str(len(body) if length is None else length))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            h.send_header(key, value)
        h.end_headers()
        if body:
            h.wfile.write(body)

    def json(self, h, status: int, obj):
        body = (json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n").encode()
        self.send(h, status, "application/json", body)

    def route_get(self, h):
        request = urlsplit(h.path)
        path = request.path
        params = parse_qs(request.query)
        if path == "/health":
            return self.json(h, 200, {"ok": True, "synthetic": True, "seed": self.seed,
                                      "scenarios": ["success", "429", "503", "redirect",
                                                    "invalid-mime", "size", "empty", "timeout"]})
        if path == "/file.epub":
            return self.send(h, 200, "application/epub+zip", self.artifact.read_bytes())
        if path == "/empty":
            return self.send(h, 200, "application/epub+zip", b"")
        if path == "/html":
            return self.send(h, 200, "text/html", b"<html>fictional error</html>")
        if path == "/wrong-mime":
            return self.send(h, 200, "application/json", b'{"not":"an epub"}')
        if path == "/invalid-epub":
            return self.send(h, 200, "application/epub+zip", b"not an epub")
        if path == "/declared-large":
            return self.send(h, 200, "application/epub+zip", b"", length=99999999)
        if path == "/stream-large":
            return self.send(h, 200, "application/epub+zip", LARGE, include_length=False)
        if path == "/status/429":
            return self.send(h, 429, "text/plain", b"synthetic rate limit",
                             extra={"Retry-After": "2"})
        if path == "/status/503":
            return self.send(h, 503, "text/plain", b"synthetic unavailable")
        if path == "/redirect/relative":
            return self.send(h, 302, "text/plain", b"", extra={"Location": "/file.epub"})
        if path == "/redirect/missing":
            return self.send(h, 302, "text/plain", b"")
        if path == "/redirect/loop-a":
            return self.send(h, 302, "text/plain", b"",
                             extra={"Location": "/redirect/loop-b"})
        if path == "/redirect/loop-b":
            return self.send(h, 302, "text/plain", b"",
                             extra={"Location": "/redirect/loop-a"})
        if path == "/redirect/loopback-other":
            # A synthetic cross-origin redirect, restricted to this machine.
            port = (params.get("port") or [""])[0]
            if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
                return self.json(h, 400, {"error": "invalid loopback port"})
            return self.send(h, 302, "text/plain", b"", extra={
                "Location": f"http://127.0.0.1:{int(port)}/inspect-headers",
            })
        if path == "/inspect-headers":
            # Never echo actual token values: return presence booleans only.
            lower = {key.casefold() for key in h.headers.keys()}
            return self.json(h, 200, {
                "authorization_present": "authorization" in lower,
                "cookie_present": "cookie" in lower,
                "custom_present": "x-test-only" in lower,
            })
        if path == "/slow":
            time.sleep(1.6)
            return self.send(h, 200, "application/epub+zip", self.artifact.read_bytes())
        return self.json(h, 404, {"error": "no such synthetic endpoint"})


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeTransport/1.0"
    app: TransportFakeApp

    def do_GET(self):
        try:
            self.app.route_get(self)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        # Avoid persisting request URLs and any credentials in fixture logs.
        sys.stderr.write("[http-fake] request served\n")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8102)
    p.add_argument("--cache-dir", required=True, type=Path)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--rebuild", action="store_true")
    args = p.parse_args(argv)
    if args.bind not in ("127.0.0.1", "localhost"):
        raise SystemExit("Le faux transport accepte localhost uniquement.")
    Handler.app = TransportFakeApp(args.cache_dir, seed=args.seed, rebuild=args.rebuild)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[server] synthetic HTTP transport on 127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
