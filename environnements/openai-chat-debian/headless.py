#!/usr/bin/env python3
"""Start Xvfb and a private D-Bus session, run a command, and clean up."""
from __future__ import annotations

from contextlib import ExitStack
import os
import argparse
from pathlib import Path
import re
import secrets
import select
import socket
import struct
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent


def stop(process) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main() -> int:
    invocation = sys.argv[1:]
    if invocation[:1] in (["--"], ["--screen"]):
        if "--" in invocation:
            separator = invocation.index("--")
            options, command = invocation[:separator], invocation[separator + 1:]
        elif invocation[:1] == ["--screen"]:
            parser = argparse.ArgumentParser(description=__doc__)
            parser.add_argument("--screen", default="1060x700x24", help="Virtual screen size WIDTHxHEIGHTx24")
            parser.error("--screen must be followed by -- and an application command")
        else:
            options, command = [], invocation[1:]
    elif invocation[:1] in (["-h"], ["--help"]):
        options, command = invocation, []
    else:
        options, command = [], invocation
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", default="1060x700x24", help="Virtual screen size WIDTHxHEIGHTx24")
    args = parser.parse_args(options)
    dimensions = re.fullmatch(r"([1-9]\d{2,3})x([1-9]\d{2,3})x24", args.screen)
    if not dimensions or any(not 320 <= int(value) <= 4096 for value in dimensions.groups()):
        parser.error("--screen must be WIDTHxHEIGHTx24 with dimensions from 320 to 4096")
    runtime = BASE / "runtime"
    root = runtime / "root"
    evidence = BASE / "evidence"
    evidence.mkdir(exist_ok=True)
    # Use loopback TCP with private X11 auth so the display can stay local and
    # work in restricted/offscreen Linux environments without a desktop socket.
    number = None
    for candidate in range(91, 191):
        try:
            sock = socket.socket()
        except PermissionError as error:
            raise RuntimeError(
                "The host denied a local TCP socket; Xvfb requires loopback access."
            ) from error
        with sock:
            try:
                sock.bind(("127.0.0.1", 6000 + candidate))
                number = candidate
                break
            except PermissionError as error:
                raise RuntimeError(
                    "The host denied a local TCP socket; Xvfb requires loopback access."
                ) from error
            except OSError:
                continue
    if number is None:
        raise RuntimeError("No free X11 port found.")
    authority = runtime / f"xauthority-{os.getpid()}"
    record = struct.pack("!H", 65535)  # FamilyWild with a random private cookie.
    for value in [b"", str(number).encode(), b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16)]:
        record += struct.pack("!H", len(value)) + value
    authority.write_bytes(record)
    authority.chmod(0o600)
    env = {**os.environ, "DISPLAY": f"127.0.0.1:{number}", "XAUTHORITY": str(authority), "CODEX_HEADLESS_SCREEN": args.screen}
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    xvfb_binary = root / "usr/bin/Xvfb-local"
    if not xvfb_binary.exists():
        raise RuntimeError("Run python3 bootstrap.py first.")
    with ExitStack() as stack:
        stack.callback(authority.unlink, missing_ok=True)
        xvfb_log = stack.enter_context((evidence / "xvfb.log").open("w"))
        xvfb = subprocess.Popen([
            str(xvfb_binary), f":{number}", "-screen", "0", args.screen,
            "-nolock", "-nolisten", "local", "-nolisten", "unix", "-listen", "tcp",
            "-auth", str(authority), "-xkbdir", str(root / "usr/share/X11/xkb"),
        ], cwd=root / "usr", env=env, stdout=xvfb_log, stderr=subprocess.STDOUT)
        stack.callback(stop, xvfb)
        deadline = time.monotonic() + 8
        while True:
            if xvfb.poll() is not None:
                raise RuntimeError("Xvfb failed; see evidence/xvfb.log.")
            try:
                with socket.create_connection(("127.0.0.1", 6000 + number), timeout=0.1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Xvfb did not start within 8 seconds.")
                time.sleep(0.05)

        # Avoid session.conf's standard filesystem paths on a VM with no desktop.
        bus_config = runtime / "dbus-session.conf"
        bus_config.write_text(f'''<busconfig>
<type>session</type>
<listen>tcp:host=127.0.0.1,port=0,family=ipv4</listen>
<auth>ANONYMOUS</auth>
<allow_anonymous/>
<policy context="default">
<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>
</policy>
</busconfig>''')
        bus_log = stack.enter_context((evidence / "dbus.log").open("w"))
        bus = subprocess.Popen([
            str(root / "usr/bin/dbus-daemon"), "--nofork", "--nopidfile",
            f"--config-file={bus_config}", "--print-address=1",
        ], env=env, stdout=subprocess.PIPE, stderr=bus_log, text=True)
        stack.callback(stop, bus)
        if not select.select([bus.stdout], [], [], 5)[0]:
            raise RuntimeError("The private D-Bus session did not start.")
        address = bus.stdout.readline().strip()
        if not address:
            raise RuntimeError("D-Bus returned no address; see evidence/dbus.log.")
        env["DBUS_SESSION_BUS_ADDRESS"] = address
        if not command:
            command = [sys.executable, "test_ui.py"]
        client = subprocess.Popen(command, cwd=BASE, env=env)
        stack.callback(stop, client)
        try:
            return client.wait()
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"headless: {error}", file=sys.stderr)
        raise SystemExit(1)
