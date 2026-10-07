#!/usr/bin/env python3
"""Build a rootless GTK4 runtime from signed Debian 13 packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parent
PACKAGES = [
    "python3-gi", "python3-gi-cairo", "gir1.2-gtk-4.0", "gir1.2-adw-1",
    "xvfb", "xauth", "libxtst6", "python3-pil", "fonts-dejavu-core",
    "x11-xkb-utils", "xkb-data", "dbus-daemon",
    "python3-packaging", "python3-httpx",
]


def parse_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def find_keyring(candidates: list[str]) -> Path:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    raise SystemExit(
        "Archive keyring not found. Install the distribution archive-keyring package "
        "or provide a host with the standard keyring installed."
    )


def distro_profile(os_release: dict[str, str]) -> dict:
    distro = os_release.get("ID", "")
    codename = os_release.get("VERSION_CODENAME", "")
    if distro == "debian" and codename == "trixie":
        keyring = find_keyring([
            "/usr/share/keyrings/debian-archive-keyring.pgp",
            "/usr/share/keyrings/debian-archive-keyring.gpg",
        ])
        return {
            "id": "debian",
            "codename": "trixie",
            "label": "Debian 13 (trixie)",
            "python": "3.13",
            "keyring": str(keyring),
            "sources": [
                f"deb [signed-by={keyring}] https://deb.debian.org/debian trixie main",
                f"deb [signed-by={keyring}] https://deb.debian.org/debian trixie-updates main",
                f"deb [signed-by={keyring}] https://security.debian.org/debian-security trixie-security main",
            ],
        }
    raise SystemExit(
        f"Unsupported host: {distro or '?'} {codename or '?'}. "
        "This recipe supports Debian 13 (trixie) only."
    )


def relocate_xvfb(root: Path) -> dict:
    """Relocate two built-in Xvfb paths; the original binary remains untouched."""
    original = root / "usr/bin/Xvfb"
    data = original.read_bytes()
    before = hashlib.sha256(data).hexdigest()
    replacements = [
        (b"/usr/bin\0", b"./aux\0"),
        (b"/var/lib/xkb/\0", b"./xkb-cache/\0"),
    ]
    for old, new in replacements:
        if data.count(old) != 1 or len(new) > len(old):
            raise RuntimeError(f"Unexpected Xvfb binary layout for {old!r}; no patch applied.")
        data = data.replace(old, new.ljust(len(old), b"\0"))
    local = root / "usr/bin/Xvfb-local"
    local.write_bytes(data)
    local.chmod(0o755)
    (root / "usr/xkb-cache").mkdir(exist_ok=True)
    cache_link = root / "usr/share/X11/xkb/xkb-cache"
    cache_link.parent.mkdir(parents=True, exist_ok=True)
    if cache_link.is_symlink():
        cache_link.unlink()
    if not cache_link.exists():
        cache_link.symlink_to("../../../xkb-cache", target_is_directory=True)
    aux = root / "usr/aux"
    aux.mkdir(exist_ok=True)
    wrapper = aux / "xkbcomp"
    wrapper.write_text('''#!/usr/bin/env bash
set -euo pipefail
local_usr="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
arguments=()
for argument in "$@"; do
    case "$argument" in
        ./xkb-cache/*) argument="$local_usr/xkb-cache/${argument#./xkb-cache/}" ;;
    esac
    arguments+=("$argument")
done
exec "$local_usr/bin/xkbcomp" "${arguments[@]}"
''')
    wrapper.chmod(0o755)
    return {
        "original_sha256": before,
        "relocated_sha256": hashlib.sha256(data).hexdigest(),
        "changes": [
            "XKB compiler directory: /usr/bin -> ./aux (path wrapper)",
            "XKB cache: /var/lib/xkb/ -> ./xkb-cache/",
        ],
    }


def archive_records(archives: list[Path], env: dict[str, str]) -> list[dict]:
    records = []
    for archive in archives:
        fields = subprocess.check_output([
            "dpkg-deb", "-f", str(archive), "Package", "Version", "Architecture"
        ], env=env, text=True).strip().splitlines()
        records.append({
            "file": archive.name,
            "metadata": fields,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        })
    return records


def extract_archives(archives: list[Path], root: Path, env: dict[str, str]) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for archive in archives:
        with subprocess.Popen(
            ["dpkg-deb", "--fsys-tarfile", str(archive)],
            stdout=subprocess.PIPE,
            env=env,
        ) as unpack:
            subprocess.run([
                "tar", "--extract", "--file=-", "--directory", str(root),
                "--no-same-owner", "--no-same-permissions",
            ], stdin=unpack.stdout, env=env, check=True)
            assert unpack.stdout is not None
            unpack.stdout.close()
            if unpack.wait() != 0:
                raise RuntimeError(f"Could not extract {archive.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Reuse cached .deb files only")
    parser.add_argument("--download-only", action="store_true", help="Download the complete .deb cache, then stop")
    parser.add_argument("--python", default=sys.executable, help="Host Python executable")
    parser.add_argument(
        "--deb-cache", type=Path,
        help="Portable directory used for .deb files (default: runtime/deb-cache/<profile>)",
    )
    args = parser.parse_args()
    if args.offline and args.download_only:
        parser.error("--offline and --download-only cannot be combined")

    os_release = parse_os_release()
    profile = distro_profile(os_release)
    if platform.machine() != "x86_64":
        raise SystemExit("This kit currently targets x86_64 only.")
    version = subprocess.check_output([
        args.python, "-c", "import sys; print('%s.%s' % sys.version_info[:2])"
    ], text=True).strip()
    if version != profile["python"]:
        raise SystemExit(
            f"{profile['label']} bindings require Python {profile['python']}; got {version}."
        )

    runtime = BASE / "runtime"
    apt = runtime / "apt"
    root = runtime / "root"
    temp = runtime / "tmp"
    cache = (args.deb_cache or runtime / "deb-cache" / f"{profile['id']}-{profile['codename']}-amd64").resolve()
    for directory in [temp, cache, cache / "partial", apt / "lists/partial", apt / "log", apt / "empty.d"]:
        directory.mkdir(parents=True, exist_ok=True)

    marker = cache / ".gtk4-cache-profile.json"
    expected_marker = {
        "distro": profile["id"], "codename": profile["codename"], "architecture": "amd64"
    }
    if marker.exists():
        found_marker = json.loads(marker.read_text())
        if found_marker != expected_marker:
            raise SystemExit(f"Cache profile mismatch: expected {expected_marker}, found {found_marker}")
    elif args.offline:
        raise SystemExit(
            f"Offline cache marker missing: {marker}. Prepare the cache first with --download-only."
        )

    empty_status = apt / "empty.status"
    empty_status.write_text("")
    sources = "\n".join(profile["sources"]) + "\n"
    (apt / "sources.list").write_text(sources)
    (apt / "empty.conf").touch()
    (apt / "apt.conf").write_text(f'''Dir::State "{apt}";
Dir::State::lists "{apt}/lists";
Dir::State::status "{empty_status}";
Dir::State::extended_states "{apt}/extended_states";
Dir::Cache "{apt}";
Dir::Cache::archives "{cache}";
Dir::Log "{apt}/log";
Dir::Etc::sourcelist "{apt}/sources.list";
Dir::Etc::sourceparts "{apt}/empty.d";
Dir::Etc::parts "{apt}/empty.d";
Dir::Etc::main "{apt}/empty.conf";
APT::Sandbox::User "{pwd.getpwuid(os.getuid()).pw_name}";
Debug::NoLocking "true";
Acquire::Languages "none";
Acquire::Retries "2";
Acquire::https::Timeout "30";
Acquire::http::Timeout "30";
''')
    env = {
        **os.environ,
        "TMPDIR": str(temp),
        "APT_CONFIG": str(apt / "apt.conf"),
        "TAR_OPTIONS": "--no-same-owner --no-same-permissions",
    }

    if not args.offline:
        subprocess.run(["/usr/bin/apt-get", "update", "--error-on=any"], env=env, check=True)
        subprocess.run([
            "/usr/bin/apt-get", "--download-only", "--no-install-recommends",
            "-y", "install", *PACKAGES,
        ], env=env, check=True)
        marker.write_text(json.dumps(expected_marker, indent=2) + "\n")

    archives = sorted(cache.glob("*.deb"))
    if not archives:
        raise SystemExit("No .deb packages cached. Run without --offline first.")
    records = archive_records(archives, env)
    lock = {
        "platform": platform.platform(),
        "host": {
            "id": profile["id"], "codename": profile["codename"],
            "python": version, "architecture": platform.machine(),
        },
        "requested_packages": PACKAGES,
        "sources": profile["sources"],
        "deb_cache": str(cache),
        "packages": records,
    }
    if args.download_only:
        (BASE / "packages-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
        print(f"Downloaded {len(archives)} packages into {cache}")
        print("Copy that cache directory with the kit, then run: python3 bootstrap.py --offline")
        return

    extract_archives(archives, root, env)
    relocation = relocate_xvfb(root)
    lock["xvfb_relocation"] = relocation
    (BASE / "packages-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n")

    run_env = {**os.environ, "GTK4_POC_PYTHON": str(Path(args.python).resolve())}
    subprocess.run([str(BASE / "gtk-python"), "check_import.py"], cwd=BASE, env=run_env, check=True)


if __name__ == "__main__":
    main()
