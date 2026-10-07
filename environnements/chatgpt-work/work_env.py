#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import time
import traceback

BASE = Path(__file__).resolve().parent
PACKAGES = [
    "python3-gi", "python3-gi-cairo", "gir1.2-gtk-4.0", "gir1.2-adw-1",
    "xvfb", "xauth", "libxtst6", "python3-pil", "fonts-dejavu-core",
    "x11-xkb-utils", "xkb-data", "dbus-daemon",
]


def relocate_xvfb(root: Path) -> dict:
    original = root / "usr/bin/Xvfb"
    data = original.read_bytes()
    before = hashlib.sha256(data).hexdigest()
    for old, new in [(b"/usr/bin\0", b"./aux\0"), (b"/var/lib/xkb/\0", b"./xkb-cache/\0")]:
        if data.count(old) != 1 or len(new) > len(old):
            raise RuntimeError(f"Disposition Xvfb inattendue pour {old!r}")
        data = data.replace(old, new.ljust(len(old), b"\0"))
    local = root / "usr/bin/Xvfb-local"
    local.write_bytes(data)
    local.chmod(0o755)
    (root / "usr/xkb-cache").mkdir(exist_ok=True)
    cache_link = root / "usr/share/X11/xkb/xkb-cache"
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
    return {"original_sha256": before, "relocated_sha256": hashlib.sha256(data).hexdigest()}


def cmd_bootstrap(args: argparse.Namespace) -> int:
    os_release = dict(
        line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
    )
    if os_release.get("VERSION_CODENAME", "").strip('"') != "noble":
        raise SystemExit("Ubuntu 24.04 (noble) est requis.")
    version = subprocess.check_output([args.python, "-c", "import sys; print('%s.%s' % sys.version_info[:2])"], text=True).strip()
    if version != "3.12":
        raise SystemExit(f"Python 3.12 requis, version détectée : {version}")
    if platform.machine() != "x86_64":
        raise SystemExit("Architecture x86_64 requise.")

    runtime = BASE / "runtime"
    apt = runtime / "apt"
    root = runtime / "root"
    temp = runtime / "tmp"
    for directory in [root, temp, apt / "lists/partial", apt / "archives/partial", apt / "log", apt / "empty.d"]:
        directory.mkdir(parents=True, exist_ok=True)
    empty_status = apt / "empty.status"
    empty_status.touch(exist_ok=True)
    host_snapshot = Path("/etc/apt/sources.list.d/caas-snapshot.list")
    if host_snapshot.exists():
        sources = host_snapshot.read_text().replace(" main restricted universe multiverse", " main universe")
    else:
        sources = "\n".join([
            "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble main universe",
            "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu noble-updates main universe",
            "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://security.ubuntu.com/ubuntu noble-security main universe",
        ]) + "\n"
    (apt / "sources.list").write_text(sources)
    (apt / "empty.conf").touch()
    (apt / "apt.conf").write_text(f'''Dir::State "{apt}";
Dir::State::lists "{apt}/lists";
Dir::State::status "{empty_status}";
Dir::State::extended_states "{apt}/extended_states";
Dir::Cache "{apt}";
Dir::Cache::archives "{apt}/archives";
Dir::Log "{apt}/log";
Dir::Etc::sourcelist "{apt}/sources.list";
Dir::Etc::sourceparts "{apt}/empty.d";
Dir::Etc::parts "{apt}/empty.d";
Dir::Etc::main "{apt}/empty.conf";
APT::Sandbox::User "{pwd.getpwuid(os.getuid()).pw_name}";
Debug::NoLocking "true";
Acquire::Languages "none";
Acquire::Retries "1";
Acquire::https::Timeout "30";
Acquire::http::Timeout "30";
''')
    env = {**os.environ, "TMPDIR": str(temp), "APT_CONFIG": str(apt / "apt.conf"), "TAR_OPTIONS": "--no-same-owner --no-same-permissions"}
    if not args.offline:
        subprocess.run(["/usr/bin/apt-get", "update", "--error-on=any"], env=env, check=True)
        subprocess.run(["/usr/bin/apt-get", "--download-only", "--no-install-recommends", "--reinstall", "-y", "install", *PACKAGES], env=env, check=True)
    archives = sorted((apt / "archives").glob("*.deb"))
    if not archives:
        raise SystemExit("Aucun .deb en cache. Relancer sans --offline.")
    records = []
    for archive in archives:
        with subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(archive)], stdout=subprocess.PIPE, env=env) as unpack:
            subprocess.run(["tar", "--extract", "--file=-", "--directory", str(root), "--no-same-owner", "--no-same-permissions"], stdin=unpack.stdout, env=env, check=True)
            assert unpack.stdout is not None
            unpack.stdout.close()
            if unpack.wait() != 0:
                raise RuntimeError(f"Extraction impossible : {archive.name}")
        fields = subprocess.check_output(["dpkg-deb", "-f", str(archive), "Package", "Version", "Architecture"], env=env, text=True).strip().splitlines()
        records.append({"file": archive.name, "metadata": fields, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
    venv = BASE / ".venv"
    subprocess.run([args.python, "-m", "venv", "--system-site-packages", "--without-pip", str(venv)], check=True)
    site_packages = next((venv / "lib").glob("python*/site-packages"))
    (site_packages / "gtk4-local.pth").write_text(str(root / "usr/lib/python3/dist-packages") + "\n")
    relocation = relocate_xvfb(root)
    (runtime / "packages-lock.json").write_text(json.dumps({
        "platform": platform.platform(), "requested_packages": PACKAGES,
        "sources": sources.splitlines(), "packages": records, "xvfb_relocation": relocation,
    }, ensure_ascii=False, indent=2) + "\n")
    print("Bootstrap OK. Vérifier avec : ./work check-import")
    return 0


def cmd_check_import(_: argparse.Namespace) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk
    adw = None
    if all(hasattr(Adw, n) for n in ("get_major_version", "get_minor_version", "get_micro_version")):
        adw = f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"
    print(json.dumps({
        "gtk_import": "OK", "adwaita_import": "OK",
        "python_version": sys.version.split()[0], "pygobject": gi.__version__,
        "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        "adwaita": adw,
        "glib": f"{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}",
        "gsk_renderer": os.environ.get("GSK_RENDERER"), "display": os.environ.get("DISPLAY"),
    }, indent=2))
    return 0


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def headless_env(screen: str, stack: ExitStack) -> dict[str, str]:
    dimensions = re.fullmatch(r"([1-9]\d{2,3})x([1-9]\d{2,3})x24", screen)
    if not dimensions or any(not 320 <= int(v) <= 4096 for v in dimensions.groups()):
        raise RuntimeError("--screen doit être WIDTHxHEIGHTx24, dimensions 320..4096")
    runtime = BASE / "runtime"
    root = runtime / "root"
    evidence = BASE / "evidence"
    evidence.mkdir(exist_ok=True)
    number = None
    for candidate in range(91, 191):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", 6000 + candidate))
                number = candidate
                break
            except PermissionError as error:
                raise RuntimeError("La VM interdit les sockets TCP locales requises par Xvfb.") from error
            except OSError:
                continue
    if number is None:
        raise RuntimeError("Aucun port X11 libre.")
    authority = runtime / f"xauthority-{os.getpid()}"
    record = struct.pack("!H", 65535)
    for value in [b"", str(number).encode(), b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16)]:
        record += struct.pack("!H", len(value)) + value
    authority.write_bytes(record)
    authority.chmod(0o600)
    stack.callback(authority.unlink, missing_ok=True)
    env = {**os.environ, "DISPLAY": f"127.0.0.1:{number}", "XAUTHORITY": str(authority), "CIFERECA_HEADLESS_SCREEN": screen}
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    xvfb_binary = root / "usr/bin/Xvfb-local"
    if not xvfb_binary.exists():
        raise RuntimeError("Bootstrap absent : ./work bootstrap")
    xvfb_log = stack.enter_context((evidence / "xvfb.log").open("w"))
    xvfb = subprocess.Popen([
        str(xvfb_binary), f":{number}", "-screen", "0", screen,
        "-nolock", "-nolisten", "local", "-nolisten", "unix", "-listen", "tcp",
        "-auth", str(authority), "-xkbdir", str(root / "usr/share/X11/xkb"),
    ], cwd=root / "usr", env=env, stdout=xvfb_log, stderr=subprocess.STDOUT)
    stack.callback(stop, xvfb)
    deadline = time.monotonic() + 8
    while True:
        if xvfb.poll() is not None:
            raise RuntimeError("Xvfb a échoué ; voir evidence/xvfb.log")
        try:
            with socket.create_connection(("127.0.0.1", 6000 + number), timeout=0.1):
                break
        except OSError:
            if time.monotonic() > deadline:
                raise RuntimeError("Xvfb n'a pas démarré sous 8 secondes.")
            time.sleep(0.05)
    bus_config = runtime / "dbus-session.conf"
    bus_config.write_text('''<busconfig>
<type>session</type>
<listen>tcp:host=127.0.0.1,port=0,family=ipv4</listen>
<auth>ANONYMOUS</auth><allow_anonymous/>
<policy context="default"><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy>
</busconfig>''')
    bus_log = stack.enter_context((evidence / "dbus.log").open("w"))
    bus = subprocess.Popen([str(root / "usr/bin/dbus-daemon"), "--nofork", "--nopidfile", f"--config-file={bus_config}", "--print-address=1"], env=env, stdout=subprocess.PIPE, stderr=bus_log, text=True)
    stack.callback(stop, bus)
    assert bus.stdout is not None
    if not select.select([bus.stdout], [], [], 5)[0]:
        raise RuntimeError("D-Bus privé non démarré.")
    address = bus.stdout.readline().strip()
    if not address:
        raise RuntimeError("Adresse D-Bus absente ; voir evidence/dbus.log")
    env["DBUS_SESSION_BUS_ADDRESS"] = address
    return env


def cmd_headless(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise RuntimeError("Commande requise après --")
    with ExitStack() as stack:
        env = headless_env(args.screen, stack)
        client = subprocess.Popen(command, cwd=BASE, env=env)
        stack.callback(stop, client)
        try:
            return client.wait()
        except KeyboardInterrupt:
            return 130


class X11Input:
    def __init__(self):
        self.x11 = C.CDLL("libX11.so.6")
        self.xtst = C.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.argtypes = [C.c_char_p]
        self.x11.XOpenDisplay.restype = C.c_void_p
        self.x11.XCloseDisplay.argtypes = [C.c_void_p]
        self.x11.XSync.argtypes = [C.c_void_p, C.c_int]
        self.x11.XStringToKeysym.argtypes = [C.c_char_p]
        self.x11.XStringToKeysym.restype = C.c_ulong
        self.x11.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        self.x11.XKeysymToKeycode.restype = C.c_ubyte
        self.xtst.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
        self.xtst.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Display X11 inaccessible.")

    def click(self, x: int, y: int) -> None:
        self.xtst.XTestFakeMotionEvent(self.display, -1, x, y, 0)
        self.xtst.XTestFakeButtonEvent(self.display, 1, True, 0)
        self.xtst.XTestFakeButtonEvent(self.display, 1, False, 0)
        self.x11.XSync(self.display, False)

    def key(self, keysym: str, pressed: bool) -> None:
        code = self.x11.XKeysymToKeycode(self.display, self.x11.XStringToKeysym(keysym.encode("ascii")))
        if not code:
            raise RuntimeError(f"Keysym inconnu : {keysym}")
        self.xtst.XTestFakeKeyEvent(self.display, code, pressed, 0)
        self.x11.XSync(self.display, False)

    def type_text(self, text: str) -> None:
        shifted = {**dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")), "!":"1", "@":"2", "#":"3", "$":"4", "%":"5", "^":"6", "&":"7", "*":"8", "(":"9", ")":"0", "_":"minus", "+":"equal", "{":"bracketleft", "}":"bracketright", "|":"backslash", ":":"semicolon", '"':"apostrophe", "<":"comma", ">":"period", "?":"slash", "~":"grave"}
        for char in text:
            if char == " ":
                keysym, shift = "space", False
            elif char in shifted:
                keysym, shift = shifted[char], True
            else:
                keysym, shift = char, False
            if shift:
                self.key("Shift_L", True)
            self.key(keysym, True); self.key(keysym, False)
            if shift:
                self.key("Shift_L", False)

    def close(self) -> None:
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


def coordinate(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-9]+),([0-9]+)", value)
    if not match:
        raise argparse.ArgumentTypeError("format attendu X,Y")
    return tuple(map(int, match.groups()))


def cmd_capture(args: argparse.Namespace) -> int:
    from PIL import Image, ImageGrab
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise RuntimeError("Commande applicative requise après --")
    output = args.out.resolve()
    report_path = output.with_suffix(".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "result":"FAIL", "backend":"GDK X11 / Xvfb", "screen": args.screen, "actions": [], "screenshot": None}
    with ExitStack() as stack:
        env = headless_env(args.screen, stack)
        process = subprocess.Popen(command, cwd=args.cwd.resolve(), env=env)
        stack.callback(stop, process)
        report["pid"] = process.pid
        try:
            deadline = time.monotonic() + args.wait
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Application fermée au démarrage (code {process.returncode})")
                time.sleep(0.025)
            device = X11Input() if (args.click or args.type_text or args.key) else None
            if device:
                stack.callback(device.close)
            for x, y in args.click:
                assert device is not None
                device.click(x, y); report["actions"].append({"type":"click","x":x,"y":y}); time.sleep(0.15)
            if args.type_text:
                if not args.type_text.isascii() or not args.type_text.isprintable():
                    raise ValueError("--type-text accepte uniquement de l'ASCII imprimable")
                assert device is not None
                device.type_text(args.type_text); report["actions"].append({"type":"keyboard_text","character_count":len(args.type_text)}); time.sleep(0.2)
            for key in args.key:
                assert device is not None
                device.key(key, True); device.key(key, False); report["actions"].append({"type":"key","keysym":key}); time.sleep(0.1)
            if process.poll() is not None:
                raise RuntimeError(f"Application fermée avant capture (code {process.returncode})")
            previous_display = os.environ.get("DISPLAY")
            previous_xauthority = os.environ.get("XAUTHORITY")
            os.environ["DISPLAY"] = env["DISPLAY"]
            os.environ["XAUTHORITY"] = env["XAUTHORITY"]
            try:
                image = ImageGrab.grab(xdisplay=env["DISPLAY"])
            finally:
                if previous_display is None:
                    os.environ.pop("DISPLAY", None)
                else:
                    os.environ["DISPLAY"] = previous_display
                if previous_xauthority is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = previous_xauthority
            image.save(output, format="PNG")
            with Image.open(output) as loaded:
                loaded.load(); colors = loaded.convert("RGB").getcolors(maxcolors=2)
                report["screenshot"] = {"file":str(output), "width":loaded.width, "height":loaded.height, "sha256":hashlib.sha256(output.read_bytes()).hexdigest(), "nonblank": colors is None or len(colors) > 1}
            if not report["screenshot"]["nonblank"]:
                raise RuntimeError("Capture uniforme/vide")
            report["application_still_open_at_capture"] = process.poll() is None
            report["result"] = "PASS"
            rc = 0
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
            traceback.print_exc()
            rc = 1
        finally:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{report['result']}: {report['screenshot'] or report.get('error')}")
    print(f"Rapport : {report_path}")
    return rc


def cmd_reset(_: argparse.Namespace) -> int:
    import shutil
    for name in ["cache", "config", "data", "state", "home", "xdg", "tmp"]:
        shutil.rmtree(BASE / "runtime" / name, ignore_errors=True)
    shutil.rmtree(BASE / "evidence", ignore_errors=True)
    (BASE / "runtime/home/EBooks").mkdir(parents=True, exist_ok=True)
    (BASE / "evidence").mkdir(exist_ok=True)
    print("État de test Cifereca réinitialisé ; runtime/root et cache apt conservés.")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Outils rootless GTK4/libadwaita pour VM ChatGPT Work Ubuntu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("bootstrap"); p.add_argument("--offline", action="store_true"); p.add_argument("--python", default=sys.executable); p.set_defaults(fn=cmd_bootstrap)
    p = sub.add_parser("check-import"); p.set_defaults(fn=cmd_check_import)
    p = sub.add_parser("headless"); p.add_argument("--screen", default="1300x900x24"); p.add_argument("command", nargs=argparse.REMAINDER); p.set_defaults(fn=cmd_headless)
    p = sub.add_parser("capture"); p.add_argument("--screen", default="1300x900x24"); p.add_argument("--wait", type=float, default=4.0); p.add_argument("--out", type=Path, default=Path("evidence/cifereca.png")); p.add_argument("--cwd", type=Path, default=BASE); p.add_argument("--click", action="append", default=[], type=coordinate); p.add_argument("--type-text"); p.add_argument("--key", action="append", default=[]); p.add_argument("command", nargs=argparse.REMAINDER); p.set_defaults(fn=cmd_capture)
    p = sub.add_parser("reset"); p.set_defaults(fn=cmd_reset)
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.fn(args))
    except RuntimeError as error:
        print(f"work: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
