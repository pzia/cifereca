from __future__ import annotations

import argparse
import json
from pathlib import Path

from .builtin_profiles import BUILTIN_PROFILE_PLUGINS, builtin_profile
from .profiles import default_output_path


def main(argv=None) -> int:
    profile_ids = tuple(plugin.profile.id for plugin in BUILTIN_PROFILE_PLUGINS)
    parser = argparse.ArgumentParser(description="Optimise les images d'un EPUB pour une liseuse e-ink")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--profile", choices=sorted(profile_ids), default="xteink-x3")
    parser.add_argument("--max-uncompressed-mb", type=int, default=2048)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    profile = builtin_profile(args.profile)
    output = args.output or default_output_path(args.input, profile)
    from .engine import optimize_epub
    report = optimize_epub(
        args.input,
        output,
        profile=profile,
        max_uncompressed_mb=args.max_uncompressed_mb,
        progress=None if args.as_json else lambda pct, msg: print(f"[{pct:3d}%] {msg}"),
    )
    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Sortie : {report.output_path}")
        print(f"Images : {report.images_optimized}/{report.images_seen}")
        print(f"Réduction : {report.reduction_percent:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
