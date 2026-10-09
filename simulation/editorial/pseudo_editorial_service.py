#!/usr/bin/env python3
"""Serveur HTTP local de JDD éditoriaux entièrement synthétiques.

Les flux de critique sont consommables par le vrai plugin RSS/JSON Feed.
Les JSON Jeunesse/Constellations restent des fixtures de contrat : aucun
chargement implicite dans les plugins et aucun accès à une source distante.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plugin_scenarios


class EditorialFakeApp:
    def __init__(self, cache_dir: Path, seed: int, *, rebuild: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.seed = int(seed)
        self.data_dir = self.cache_dir / "specialized"
        if rebuild and self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.manifest = plugin_scenarios.create(self.data_dir, seed=self.seed)

    def _read(self, name: str) -> bytes:
        # The route table below is the *only* way to choose a file.
        if name not in self.manifest["files"]:
            raise ValueError("fixture non déclarée")
        return (self.data_dir / name).read_bytes()

    @staticmethod
    def send(handler: BaseHTTPRequestHandler, body: bytes, *, content_type: str,
             status: HTTPStatus = HTTPStatus.OK) -> None:
        handler.send_response(int(status))
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(body)

    def json(self, handler: BaseHTTPRequestHandler, value: object,
             *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send(handler, (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
                  content_type="application/json; charset=utf-8", status=status)

    def route_get(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlsplit(handler.path).path
        if path == "/health":
            youth = json.loads(self._read("youth.json"))
            criticism = json.loads(self._read("criticism.json"))
            stars = json.loads(self._read("constellations.json"))
            return self.json(handler, {
                "ok": True, "synthetic": True, "seed": self.seed,
                "counts": {
                    "youth": len(youth["records"]),
                    "criticism": len(criticism["sources"]["ux-critique-a"]["items"]),
                    "constellations": len(stars["records"]),
                },
            })
        if path == "/criticism/index.html":
            port = handler.server.server_address[1]
            page = (f'<!doctype html><html lang="fr"><head><meta charset="UTF-8">'
                    f'<title>Critiques fictives Cifereca</title>'
                    f'<link rel="alternate" type="application/rss+xml" '
                    f'href="http://127.0.0.1:{port}/criticism/rss.xml" title="RSS fictif">'
                    f'<link rel="alternate" type="application/feed+json" '
                    f'href="http://127.0.0.1:{port}/criticism/jsonfeed.json" title="JSON Feed fictif">'
                    f'</head><body><h1>Critiques fictives pour les tests</h1>'
                    f'<p>Source exclusivement synthétique.</p></body></html>')
            return self.send(handler, page.encode("utf-8"),
                             content_type="text/html; charset=utf-8")
        endpoints = {
            "/criticism/rss.xml": ("criticism.rss.xml", "application/rss+xml; charset=utf-8"),
            "/criticism/jsonfeed.json": ("criticism.jsonfeed.json", "application/feed+json; charset=utf-8"),
            "/youth/records.json": ("youth.json", "application/json; charset=utf-8"),
            "/constellations/records.json": ("constellations.json", "application/json; charset=utf-8"),
            "/criticism/cases.json": ("criticism.json", "application/json; charset=utf-8"),
        }
        if path in endpoints:
            filename, content_type = endpoints[path]
            return self.send(handler, self._read(filename), content_type=content_type)
        if path == "/criticism/empty.xml":
            return self.send(handler,
                             b'<?xml version="1.0"?><rss version="2.0"><channel><title>Vide</title></channel></rss>',
                             content_type="application/rss+xml; charset=utf-8")
        if path == "/criticism/invalid.xml":
            return self.send(handler, b"<rss><channel>incomplet",
                             content_type="application/rss+xml; charset=utf-8")
        if path == "/criticism/unavailable":
            return self.json(handler, {"error": "indisponibilite simulee"},
                             status=HTTPStatus.SERVICE_UNAVAILABLE)
        return self.json(handler, {"error": "route inexistante"},
                         status=HTTPStatus.NOT_FOUND)


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeEditorial/1.0"
    app: EditorialFakeApp

    def do_GET(self) -> None:
        try:
            self.app.route_get(self)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[editorial] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8097)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=plugin_scenarios.SEED)
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Never bind this development-only service beyond the machine.
    if args.bind not in ("127.0.0.1", "localhost"):
        raise SystemExit("Simulateur editorial : bind loopback requis")
    Handler.app = EditorialFakeApp(args.cache_dir, args.seed, rebuild=args.rebuild)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[server] editorial synthetic http://127.0.0.1:{server.server_port}/health",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
