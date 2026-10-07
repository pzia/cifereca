#!/usr/bin/env python3
"""Versioned, isolated and resettable fixtures for CIFERECA E2E scenarios."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator
import zipfile

import datasetctl


ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "e2e-fixtures"
MANIFEST_NAME = "e2e-fixture.json"


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    description: str
    profile: str
    local_epubs: int
    services: tuple[str, ...]


def fixture_path(value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.exists():
        return raw.resolve()
    name = value[:-5] if value.endswith(".toml") else value
    candidate = FIXTURES_DIR / f"{name}.toml"
    if not candidate.is_file():
        raise datasetctl.DatasetError(f"fixture E2E inconnue: {value}")
    return candidate.resolve()


def load_fixture(value: str) -> FixtureSpec:
    path = fixture_path(value)
    data = datasetctl.load_toml(path)
    if int(data.get("version") or 0) != 1:
        raise datasetctl.DatasetError(f"version de fixture E2E non supportée: {path}")
    name = str(data.get("name") or path.stem).strip()
    profile_name = str(data.get("profile") or "").strip()
    initial = data.get("initial") or {}
    if not name or not profile_name or not isinstance(initial, dict):
        raise datasetctl.DatasetError(f"fixture E2E incomplète: {path}")
    services = initial.get("services") or []
    if not isinstance(services, list) or not all(isinstance(item, str) and item for item in services):
        raise datasetctl.DatasetError(f"initial.services invalide: {path}")
    return FixtureSpec(
        name=name,
        description=str(data.get("description") or ""),
        profile=profile_name,
        local_epubs=max(0, int(initial.get("local_epubs") or 0)),
        services=tuple(sorted(services)),
    )


def fixture_profile(spec: FixtureSpec) -> dict:
    profile_path, profile = datasetctl.load_profile(spec.profile)
    profile = copy.deepcopy(profile)
    profile["_path"] = str(profile_path)
    # Never reuse the normal UX runtime or a real user's installed plugin store.
    profile["name"] = f"e2e-{spec.name}"
    cifereca = dict(profile.get("cifereca") or {})
    cifereca["copy_installed_plugins"] = False
    profile["cifereca"] = cifereca

    local_epubs = int((profile.get("library") or {}).get("epubs") or 0)
    services = tuple(sorted(datasetctl.enabled_service_configs(profile)))
    if local_epubs != spec.local_epubs:
        raise datasetctl.DatasetError(
            f"{spec.name}: local_epubs attendu={spec.local_epubs} profil={local_epubs}"
        )
    if services != spec.services:
        raise datasetctl.DatasetError(
            f"{spec.name}: services attendus={spec.services!r} profil={services!r}"
        )
    return profile


def _stable_file_payload(path: Path) -> bytes:
    if path.suffix.casefold() == ".epub":
        digest = hashlib.sha256()
        with zipfile.ZipFile(path) as archive:
            for info in sorted(
                (item for item in archive.infolist() if not item.is_dir()),
                key=lambda item: item.filename,
            ):
                digest.update(info.filename.encode("utf-8", "surrogatepass"))
                digest.update(b"\0")
                digest.update(archive.read(info.filename))
                digest.update(b"\0")
        return digest.digest()
    return path.read_bytes()


def tree_digest(root: Path) -> str:
    """Hash logical fixture content, ignoring volatile ZIP metadata such as timestamps."""
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_stable_file_payload(path))
        digest.update(b"\0")
    return digest.hexdigest()


def reset_fixture(value: str) -> None:
    spec = load_fixture(value)
    profile = fixture_profile(spec)
    runtime = datasetctl.profile_runtime(profile)
    datasetctl.reset_runtime(profile)
    (runtime / MANIFEST_NAME).unlink(missing_ok=True)


def prepare_fixture(value: str, *, start_services: bool = True) -> dict:
    spec = load_fixture(value)
    profile = fixture_profile(spec)
    catalog = datasetctl.load_catalog()
    runtime = datasetctl.profile_runtime(profile)

    datasetctl.reset_runtime(profile)
    if start_services:
        prepared = datasetctl.start_services(profile, catalog, rebuild=True)
    else:
        prepared = datasetctl.prepare(profile, catalog, rebuild_library=True)

    env = dict(prepared["env"])
    env["CIFERECA_E2E_FIXTURE"] = spec.name
    prepared["env"] = env
    manifest = {
        "version": 1,
        "fixture": spec.name,
        "description": spec.description,
        "profile": spec.profile,
        "runtime_profile": profile["name"],
        "initial": {
            "local_epubs": spec.local_epubs,
            "services": list(spec.services),
        },
        "library_digest": tree_digest(Path(prepared["library"])),
        "config": str(prepared["config"]),
        "plugins_config": str(prepared["plugins_config"]),
        "runtime": str(runtime),
    }
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / MANIFEST_NAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    prepared["manifest"] = manifest
    prepared["profile"] = profile
    return prepared


@contextmanager
def fixture_session(value: str, *, start_services: bool = True) -> Iterator[dict]:
    prepared = prepare_fixture(value, start_services=start_services)
    try:
        yield prepared
    finally:
        reset_fixture(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Préparer des états E2E reproductibles CIFERECA")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lister les fixtures versionnées")

    describe = sub.add_parser("describe", help="Afficher la déclaration d'une fixture")
    describe.add_argument("fixture")

    prepare = sub.add_parser("prepare", help="Réinitialiser puis préparer une fixture")
    prepare.add_argument("fixture")
    prepare.add_argument("--no-services", action="store_true")

    reset = sub.add_parser("reset", help="Nettoyer complètement une fixture")
    reset.add_argument("fixture")

    run = sub.add_parser("run", help="Exécuter une commande dans la fixture puis nettoyer")
    run.add_argument("fixture")
    run.add_argument("--no-services", action="store_true")
    run.add_argument("command_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        for path in sorted(FIXTURES_DIR.glob("*.toml")):
            print(load_fixture(str(path)).name)
        return 0
    if args.command == "describe":
        print(json.dumps(load_fixture(args.fixture).__dict__, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "prepare":
        prepared = prepare_fixture(args.fixture, start_services=not args.no_services)
        print(json.dumps(prepared["manifest"], ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "reset":
        reset_fixture(args.fixture)
        return 0
    command = list(args.command_args or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise datasetctl.DatasetError("run exige une commande après --")
    with fixture_session(args.fixture, start_services=not args.no_services) as prepared:
        return subprocess.call(command, env=prepared["env"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except datasetctl.DatasetError as exc:
        print(f"e2e-fixtures: {exc}", file=sys.stderr)
        raise SystemExit(2)
