#!/usr/bin/env python3
"""Read-only recipe prerequisites and real Bubblewrap probes; never sudo/sysctl.

Only synthetic temporary directories are created. No production configuration
is imported or read. A successful bootstrap alone is not recipe acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


def read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return f"unavailable: {exc}"


def command(args: list[str], *, pass_fds: tuple[int, ...] = ()) -> dict:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20,
                                pass_fds=pass_fds, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
        return {"command": args, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": args, "returncode": None, "error": str(exc)}


def sandbox_command(bwrap: str, payload: list[str]) -> list[str]:
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--clearenv"]
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"]
    if Path("/etc/ld.so.cache").exists():
        args += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    return args + ["--remount-ro", "/", "--", *payload]


CHILD = r'''
import errno, json, os, pathlib
status = dict(line.split(':', 1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert int(status['CapEff'].strip(), 16) == 0, 'child retained effective capabilities'
assert status['NoNewPrivs'].strip() == '1', 'no_new_privs missing'
assert not pathlib.Path('/home').exists(), 'host home unexpectedly mounted'
interfaces = {line.split(':', 1)[0].strip() for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]}
assert interfaces <= {'lo'}, 'host network interface visible'
try:
    pathlib.Path('/outside-recipe').write_text('synthetic probe')
except OSError as exc:
    assert exc.errno == errno.EROFS, str(exc)
else:
    raise AssertionError('root is writable')
namespaces = {kind: os.readlink('/proc/self/ns/' + kind) for kind in ('user', 'net', 'mnt', 'pid')}
try:
    label = pathlib.Path('/proc/self/attr/current').read_text().strip()
except OSError as exc:
    label = 'unavailable: ' + str(exc)
print(json.dumps({'namespaces': namespaces, 'apparmor_label': label, 'cap_eff': status['CapEff'].strip(), 'no_new_privs': 1}))
'''


CAPABILITY_CHILD = r'''
import ctypes, errno, json, os, pathlib
label = pathlib.Path('/proc/self/attr/current').read_text().strip()
libc = ctypes.CDLL(None, use_errno=True)
libc.unshare.argtypes = [ctypes.c_int]
libc.unshare.restype = ctypes.c_int
# A fresh user namespace grants namespace-local capabilities even after
# --cap-drop ALL. Test their use; never mount, map IDs, or change host files.
result = {'status': 'allowed', 'stage': 'mount_namespace', 'errno': 0}
for stage, flag in (('user_namespace', 0x10000000), ('mount_namespace', 0x00020000)):
    if libc.unshare(flag) != 0:
        code = ctypes.get_errno()
        result = {'status': 'denied' if code in (errno.EPERM, errno.EACCES) else 'unverified',
                  'stage': stage, 'errno': code, 'error': os.strerror(code)}
        break
print(json.dumps({'apparmor_label': label, **result}))
'''


def capability_denial_verified(result: dict) -> bool:
    """Permission denial is evidence; unsupported syscalls/limits are not."""
    return (isinstance(result, dict) and result.get("status") == "denied"
            and type(result.get("errno")) is int and result["errno"] in (1, 13)
            and result.get("stage") in {"user_namespace", "mount_namespace"}
            and isinstance(result.get("apparmor_label"), str)
            and "unpriv_bwrap" in result["apparmor_label"] and "enforce" in result["apparmor_label"])


def diagnose(*, require_apparmor: bool = False, kernel_audit: bool = False,
             profile: Path | None = None) -> dict:
    report = {"schema": "cifereca.recette.doctor/1", "os_release": read("/etc/os-release"),
              "kernel": platform.release(), "uid": os.getuid(), "checks": {}, "failures": []}
    checks, failures = report["checks"], report["failures"]
    report["sysctls"] = {name: read("/proc/sys/" + name.replace(".", "/")) for name in (
        "kernel.apparmor_restrict_unprivileged_userns", "kernel.unprivileged_userns_clone",
        "user.max_user_namespaces", "user.max_net_namespaces")}
    report["parent_apparmor_label"] = read("/proc/self/attr/current")
    report["apparmor_enabled"] = read("/sys/module/apparmor/parameters/enabled")
    for binary, args in (("bwrap", ["--version"]), ("apparmor_parser", ["--version"]),
                         ("aa-status", ["--json"])):
        location = shutil.which(binary)
        checks[binary] = command([location, *args]) if location else {"error": "not installed"}
    if profile is not None:
        parser = shutil.which("apparmor_parser")
        checks["profile_syntax"] = (command([parser, "--skip-kernel-load", "--skip-cache", str(profile)])
                                    if parser else {"error": "apparmor_parser not installed"})
        if checks["profile_syntax"].get("returncode") != 0:
            failures.append("profile_syntax_unverified")
    if os.getuid() == 0:
        failures.append("run_as_recipe_user_not_root")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        failures.append("bubblewrap_not_installed")
    else:
        checks["bwrap_help"] = command([bwrap, "--help"])
        features_known = checks["bwrap_help"].get("returncode") == 0
        has_fd_bind = features_known and "--ro-bind-fd" in checks["bwrap_help"].get("stdout", "")
        if not features_known:
            failures.append("bubblewrap_features_unverified")
        elif not has_fd_bind:
            failures.append("ro_bind_fd_unavailable_for_source_symlinks")
        try:
            parent = {kind: os.readlink("/proc/self/ns/" + kind) for kind in ("user", "net", "mnt", "pid")}
        except OSError as exc:
            parent = {}
            report["namespace_error"] = str(exc)
            failures.append("parent_namespaces_unavailable")
        report["parent_namespaces"] = parent
        checks["bootstrap"] = command(sandbox_command(bwrap, ["/usr/bin/python3", "-I", "-B", "-c", CHILD]))
        if checks["bootstrap"].get("returncode") != 0:
            failures.append("bubblewrap_bootstrap_failed")
        else:
            try:
                child = json.loads(checks["bootstrap"]["stdout"])
                if any(child["namespaces"][kind] == value for kind, value in parent.items()):
                    failures.append("namespace_isolation_missing")
                if require_apparmor and ("unpriv_bwrap" not in child["apparmor_label"]
                                         or "enforce" not in child["apparmor_label"]):
                    failures.append("capability_stripping_apparmor_child_unverified")
                checks["child"] = child
            except (ValueError, KeyError, TypeError):
                failures.append("invalid_bootstrap_evidence")
            if require_apparmor:
                probe = command(sandbox_command(bwrap, ["/usr/bin/python3", "-I", "-B", "-c", CAPABILITY_CHILD]))
                checks["child_capability_probe"] = probe
                try:
                    evidence = json.loads(probe.get("stdout", ""))
                    checks["child_capability_result"] = evidence
                    verified = probe.get("returncode") == 0 and capability_denial_verified(evidence)
                except (ValueError, TypeError):
                    verified = False
                if not verified:
                    failures.append("child_capability_denial_unverified")
        if has_fd_bind:
            with tempfile.TemporaryDirectory(prefix="cifereca-doctor-") as directory:
                source = Path(directory) / "alias"
                source.symlink_to("synthetic-target")
                os.utime(source, ns=(1, 1), follow_symlinks=False)
                before = source.lstat()
                fd = os.open(directory, os.O_PATH | os.O_DIRECTORY)
                try:
                    args = sandbox_command(bwrap, ["/usr/bin/python3", "-I", "-B", "-c",
                        "import os; assert os.readlink('/source/alias') == 'synthetic-target'"])
                    position = args.index("--remount-ro")
                    args[position:position] = ["--ro-bind-fd", str(fd), "/source"]
                    checks["source_readlink"] = command(args, pass_fds=(fd,))
                finally:
                    os.close(fd)
                after = source.lstat()
                if checks["source_readlink"].get("returncode") != 0 or (
                        before.st_atime_ns, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                        after.st_atime_ns, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                    failures.append("readonly_source_link_probe_failed")
    if require_apparmor and (report["apparmor_enabled"] != "Y" or
            report["sysctls"]["kernel.apparmor_restrict_unprivileged_userns"] != "1"):
        failures.append("global_apparmor_restriction_not_verified_enabled")
    if kernel_audit:
        audit = command(["journalctl", "-k", "-b", "-n", "300", "--no-pager"])
        # Only matching kernel AppArmor events, never application/auth logs.
        audit["stdout"] = "\n".join(line for line in audit.get("stdout", "").splitlines()
                                    if "apparmor=" in line and any(word in line for word in ("bwrap", "userns", "uid_map")))
        checks["kernel_audit"] = audit
    report["ok"] = not failures
    report["scope"] = "synthetic_prerequisites_only_not_recipe_or_host_acceptance"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-apparmor", action="store_true", help="require enabled global restriction and enforcing capability-stripping child")
    parser.add_argument("--kernel-audit", action="store_true", help="read matching accessible kernel events; never elevate")
    parser.add_argument("--profile", type=Path, help="syntax-check a profile without loading it or writing a cache")
    args = parser.parse_args(argv)
    report = diagnose(require_apparmor=args.require_apparmor, kernel_audit=args.kernel_audit, profile=args.profile)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
