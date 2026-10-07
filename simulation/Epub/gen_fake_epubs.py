#!/usr/bin/env python3
"""Génère un jeu de données d'EPUB fictifs.

Le contenu, les métadonnées, les titres et les couvertures sont produits par le
socle commun ``simulation/common/fake_books.py``.

Exemples :
    python3 gen_fake_epubs.py -n 10 -o ./jdd_epub
    python3 gen_fake_epubs.py -n 1000 -o ./EBooks --seed 42
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBookFactory, slugify, write_epub


def generate(count: int, output_dir: Path, seed: int | None) -> list[Path]:
    actual_seed = 20261006 if seed is None else seed
    factory = FakeBookFactory(actual_seed, "epub")
    created: list[Path] = []
    width = max(3, len(str(count)))

    for index in range(1, count + 1):
        book = factory.make(index)
        path = output_dir / f"{index:0{width}d}-{slugify(book.title)}.epub"
        write_epub(path, book)
        created.append(path)
        print(f"{index:0{width}d}  {book.title} — {book.author} -> {path}")

    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère un jeu de données d'EPUB fictifs.")
    parser.add_argument("-n", "--count", type=int, default=10, help="Nombre d'EPUB à créer (défaut: 10)")
    parser.add_argument("-o", "--output", type=Path, default=Path("./jdd_epub"), help="Répertoire de sortie")
    parser.add_argument("--seed", type=int, default=None, help="Graine aléatoire pour un JDD reproductible")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count doit être >= 1")
    args.output.mkdir(parents=True, exist_ok=True)
    created = generate(args.count, args.output, args.seed)
    print(f"\n{len(created)} EPUB généré(s) dans {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
