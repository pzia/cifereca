"""P1 #4981: Linux-only, fail-closed runner for pure Notice C source plugins.

The plugin receives *only* admitted descriptive facts. Its Python source is
executed after kernel seccomp restrictions are installed, with no file,
network, process-spawning or storage broker capability. No plugin imports,
manifest evaluation or arbitrary package initialization precede confinement.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
from hashlib import sha256
import json
import os
import resource
import sys

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PLUGIN_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 16 * 1024

# Denied after the worker has consumed its input and initialized Python.
# The seccomp default is allow for CPU, memory and the already-owned protocol
# descriptor; use kernel enforcement, not monkeypatches or a Python-only jail.
_FORBIDDEN = (
    "open", "openat", "openat2", "creat", "socket", "socketpair",
    "connect", "bind", "listen", "accept", "accept4", "sendto",
    "sendmsg", "recvfrom", "recvmsg", "execve", "execveat",
    "fork", "vfork", "clone", "clone3", "ptrace",
    "unlink", "unlinkat", "rename", "renameat", "renameat2",
    "mkdir", "mkdirat", "rmdir", "chmod", "fchmod", "fchmodat",
    "chown", "fchown", "fchownat", "truncate", "ftruncate",
    "mount", "umount2", "link", "linkat", "symlink", "symlinkat",
)
_REQUIRED = ("openat", "socket", "connect", "execve", "clone")


def _confine() -> None:
    if sys.platform != "linux":
        raise RuntimeError("consolidator_sandbox_unavailable")
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("consolidator_sandbox_unavailable")
    lib = ctypes.CDLL(library, use_errno=True)
    lib.seccomp_init.argtypes = (ctypes.c_uint32,)
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = (
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
    )
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_load.argtypes = (ctypes.c_void_p,)
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_release.argtypes = (ctypes.c_void_p,)
    lib.seccomp_release.restype = None

    # Required for unprivileged Linux processes to load seccomp filters.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong)
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("consolidator_sandbox_unavailable")

    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("consolidator_sandbox_unavailable")
    try:
        available = set()
        deny = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO(EPERM)
        for name in _FORBIDDEN:
            syscall = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            if syscall < 0:
                continue
            available.add(name)
            if lib.seccomp_rule_add(context, deny, syscall, 0) != 0:
                raise RuntimeError("consolidator_sandbox_unavailable")
        if not set(_REQUIRED).issubset(available):
            raise RuntimeError("consolidator_sandbox_unavailable")
        if lib.seccomp_load(context) != 0:
            raise RuntimeError("consolidator_sandbox_unavailable")
    finally:
        lib.seccomp_release(context)


# Deliberately exclude __import__, open, eval, exec, globals and reflection.
# This is a defence in depth; kernel syscall denial is the I/O boundary.
_PURE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool,
    "dict": dict, "enumerate": enumerate, "float": float,
    "int": int, "len": len, "list": list, "max": max,
    "min": min, "range": range, "reversed": reversed,
    "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip,
    "ValueError": ValueError, "TypeError": TypeError,
}


def _execute(envelope: dict[str, object]) -> object:
    source = envelope.get("source")
    if type(source) is not str:
        raise ValueError("invalid_consolidator_source")
    source_bytes = source.encode("utf-8")
    if not 0 < len(source_bytes) <= MAX_PLUGIN_BYTES:
        raise ValueError("invalid_consolidator_source")
    if sha256(source_bytes).hexdigest() != envelope.get("source_sha256"):
        raise ValueError("consolidator_source_mismatch")
    facts = envelope.get("facts")
    if type(facts) is not dict:
        raise ValueError("invalid_consolidator_input")
    # Compilation, module-level initialization and execution all occur AFTER
    # the sandbox. Any forbidden syscall raises EPERM or fails closed.
    _confine()
    namespace: dict[str, object] = {"__builtins__": _PURE_BUILTINS}
    exec(compile(source, "<installed-notice-consolidator>", "exec"), namespace)
    consolidate = namespace.get("consolidate")
    if not callable(consolidate):
        raise ValueError("consolidator_entrypoint_missing")
    return consolidate(facts)


def main() -> None:
    # Keep the wire descriptor private; plugin stdout/stderr cannot pollute
    # structured output even if it attempts print/os.write.
    protocol_fd = os.dup(1)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("consolidator_request_unbounded")
        envelope = json.loads(raw)
        if type(envelope) is not dict:
            raise ValueError("invalid_consolidator_request")
        # No inherited env/credential is available when plugin code runs.
        os.environ.clear()
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024, 64 * 1024))
        with open(os.devnull, "wb", buffering=0) as null_output:
            os.dup2(null_output.fileno(), 1)
            os.dup2(null_output.fileno(), 2)
        result = _execute(envelope)
        response = {"ok": True, "result": result}
        payload = json.dumps(
            response, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("consolidator_response_unbounded")
    except Exception:
        payload = b'{"ok":false}'
    try:
        os.write(protocol_fd, payload)
    finally:
        os.close(protocol_fd)


if __name__ == "__main__":
    main()
