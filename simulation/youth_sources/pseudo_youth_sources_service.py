#!/usr/bin/env python3
"""Faux endpoints BookTrust et OpenLibrary, isolés sur 127.0.0.1.

Les vrais plugins conservent leurs URLs publiques et allowlists : les tests
injectent un transport HTTPX de redirection *uniquement en mémoire*.
Ne jamais modifier le DNS, /etc/hosts ou les URLs utilisées en production.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
from urllib.parse import parse_qs, urlsplit

SEED = 20261009
ISBN = "9780140328721"
ISBN_OTHER = "9780306406157"
BOOK_PREFIX = "/book-recommendations/bookfinder/"
PUBLIC_BOOK = "https://www.booktrust.org.uk" + BOOK_PREFIX
DATA = (
    ("atlas-des-lucioles", "The Firefly Atlas", "Camille Example", "8 to 11", "8 to 10", "9 to 11", "Adventure"),
    ("jardin-des-mots", "The Garden of Words", "Alex Example", "2 to 5", "2 to 5", "5 to 7", "Picture books"),
    ("dernier-rivage", "The Last Shore", "Sam Example", "11 to 14", "11 to 14", "13+", "Historical"),
)


def _html(value: object) -> str:
    import html
    return html.escape(str(value), quote=True)


def booktrust_listing(*, page: int = 1, empty: bool = False,
                      age_group: str = "", categories: tuple[str, ...] = ()) -> bytes:
    # BookTrust's groups overlap; only asserted age ranges can enter a result.
    eligible = list(DATA)
    if age_group:
        import re
        group = re.search(r"(\d+)\s+to\s+(\d+)", age_group, flags=re.I)
        if group is None:
            eligible = []
        else:
            lo, hi = int(group.group(1)), int(group.group(2))
            eligible = [item for item in eligible
                        if int(item[3].split(" to ")[0]) <= hi
                        and int(item[3].split(" to ")[1]) >= lo]
    if categories:
        permitted = {c.casefold() for c in categories}
        eligible = [item for item in eligible if item[6].casefold() in permitted]
    entries = () if empty else eligible[(page - 1) * 2:page * 2]
    blocks = []
    for slug, title, author, group, interest, reading, theme in entries:
        blocks.append(
            f'<article><h3><a href="{BOOK_PREFIX}{slug}/">{_html(title)}</a></h3>'
            f'<p>by {_html(author)} 2024</p><p>For ages {group}</p>'
            f'<p>{_html(theme)}</p></article>'
        )
    return ('<!doctype html><html><body><main>' + ''.join(blocks) +
            '<a href="/book-recommendations/bookfinder/">Bookfinder</a></main></body></html>').encode()


def booktrust_detail(slug: str) -> bytes | None:
    for item in DATA:
        if item[0] != slug:
            continue
        _, title, author, _, interest, reading, theme = item
        return ('<!doctype html><html><body>'
                f'<h1>{_html(title)}</h1><p>by {_html(author)}</p>'
                f'<p>Interest age: {interest}</p><p>Reading age: {reading}</p>'
                '<p>Published by Fictional Press, 2024</p>'
                f'<p>{_html(theme)}</p><h2>About this book</h2>'
                '</body></html>').encode()
    return None


def openlibrary_docs() -> list[dict]:
    # Fixture entièrement synthétique, ISBN de structure valide seulement.
    return [
        {"key": "/works/OL45804W", "title": "The Firefly Atlas",
         "author_name": ["Camille Example"], "first_publish_year": 2024,
         "isbn": [ISBN, "0140328726"], "language": ["eng", "fre"],
         "subject": ["Adventure"], "publisher": ["Fictional Press"],
         "edition_key": ["OL7353617M"], "cover_i": 123456},
        {"key": "/works/OL98765W", "title": "Different Edition",
         "author_name": ["Camille Example"], "first_publish_year": 2023,
         "isbn": [ISBN_OTHER], "language": ["eng"],
         "subject": ["Adventure"], "publisher": ["Test Books"],
         "edition_key": ["OL888888M"]},
        {"key": "/works/OL111W", "title": "Incomplete Metadata",
         "isbn": ["invalid-isbn"], "subject": ["Mystery"], "language": []},
    ]


def openlibrary_work() -> dict:
    return {"title": "The Firefly Atlas",
            "authors": [{"author": {"key": "/authors/OL34184A"}}],
            "subjects": ["Adventure", "Fantasy"], "covers": [123456],
            "first_publish_date": "2024"}


def openlibrary_edition() -> dict:
    return {"title": "The Firefly Atlas",
            "isbn_13": [ISBN], "isbn_10": ["0140328726"],
            "languages": [{"key": "/languages/eng"}],
            "publishers": ["Fictional Press"], "publish_date": "2024"}


class YouthSourcesApp:
    def __init__(self, *, seed: int = SEED):
        self.seed = int(seed)

    @staticmethod
    def send(handler, status, content_type, payload):
        handler.send_response(int(status))
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(payload)

    def json(self, handler, status, obj):
        return self.send(handler, status, "application/json; charset=utf-8",
                         (json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))

    def route_get(self, handler):
        parts = urlsplit(handler.path)
        path = parts.path
        args = parse_qs(parts.query)
        if path == "/health":
            return self.json(handler, 200, {"ok": True, "synthetic": True, "seed": self.seed,
                                             "sources": ["booktrust", "openlibrary"],
                                             "books": len(DATA)})
        if path == "/booktrust/book-recommendations/bookfinder/bookfinder-recommendations/":
            if (args.get("fault") or [""])[0] == "unavailable":
                return self.send(handler, 503, "text/plain", b"Unavailable (simulated)")
            if (args.get("fault") or [""])[0] == "non-html":
                return self.json(handler, 200, {"error": "not HTML"})
            page = min(100, max(1, int((args.get("page") or ["1"])[0])))
            blank = (args.get("ageGroups") or [""])[0] == "empty"
            return self.send(handler, 200, "text/html; charset=utf-8",
                             booktrust_listing(page=page, empty=blank,
                                 age_group=(args.get("ageGroups") or [""])[0],
                                 categories=tuple(args.get("bookfinderCategories[]", ()))))
        if path.startswith("/booktrust" + BOOK_PREFIX):
            slug = path.rstrip("/").split("/")[-1]
            body = booktrust_detail(slug)
            if body is not None:
                return self.send(handler, 200, "text/html; charset=utf-8", body)
            return self.send(handler, 404, "text/plain", b"unknown synthetic book")
        if path == "/openlibrary/search.json":
            if (args.get("q") or [""])[0] == "rate-limit":
                return self.json(handler, 429, {"error": "simulated rate limit"})
            if (args.get("q") or [""])[0] == "broken-json":
                return self.send(handler, 200, "application/json", b'{"docs": [')
            query = (args.get("q") or [""])[0].casefold()
            page = min(100, max(1, int((args.get("page") or ["1"])[0])))
            limit = min(20, max(1, int((args.get("limit") or ["10"])[0])))
            docs = openlibrary_docs() if page == 1 else []
            if query.startswith("isbn:"):
                # Deliberately return one contradictory row: the real plugin
                # must filter by the requested ISBN before accepting it.
                docs = list(reversed(docs))
            elif query == "nothing":
                docs = []
            return self.json(handler, 200, {"numFound": len(docs), "start": (page - 1) * limit,
                                            "docs": docs[:limit]})
        if path == "/openlibrary/works/OL45804W.json":
            return self.json(handler, 200, openlibrary_work())
        if path == "/openlibrary/books/OL7353617M.json":
            return self.json(handler, 200, openlibrary_edition())
        if path.startswith("/openlibrary/works/") or path.startswith("/openlibrary/books/"):
            return self.json(handler, 404, {"error": "not found"})
        return self.json(handler, 404, {"error": "unknown fixture route"})


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeYouthSources/1.0"
    app: YouthSourcesApp

    def do_GET(self):
        try:
            self.app.route_get(self)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (TypeError, ValueError):
            self.app.json(self, 400, {"error": "invalid synthetic request"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[youth-fake] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8098)
    p.add_argument("--cache-dir", default="", help="Accepted by datasetctl; no persistent cache needed")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--rebuild", action="store_true", help="No-op; all outputs are deterministic")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    if a.bind not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Localhost uniquement")
    Handler.app = YouthSourcesApp(seed=a.seed)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[server] youth synthetic http://127.0.0.1:{a.port}/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
