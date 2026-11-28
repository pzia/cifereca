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
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "pidfd_getfd", "process_vm_readv", "process_vm_writev",
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



def projection_digest(rows):
    """Bounded canonical digest, with explicit empty distinct from unknown."""
    from hashlib import sha256
    if type(rows) is not tuple or len(rows) > 2048:
        return None
    seen = set()
    for row in rows:
        if (type(row) is not tuple or len(row) != 4
                or not all(type(value) is str and len(value) <= 4096 for value in row)
                or not row[0] or not row[1] or row[0] in seen):
            return None
        seen.add(row[0])
    payload = json.dumps(sorted(rows), ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > 2_000_000:
        return None
    return sha256(payload).hexdigest()



MAX_REPLAY_REQUEST_BYTES = 12 * 1024 * 1024


def _execute_replay(envelope):
    """Load declared pure modules only after the same kernel confinement."""
    import base64
    import builtins
    import dataclasses
    import datetime
    import html.parser
    import re
    import types
    import urllib.parse
    import __future__
    import _strptime
    import encodings.ascii
    import encodings.utf_8
    import encodings.iso8859_1
    import encodings.cp1252

    modules = envelope["modules"]
    entries = envelope["entries"]
    capability = envelope["capability"]
    if (type(modules) is not list or not 1 <= len(modules) <= 16
            or type(entries) is not list or not 1 <= len(entries) <= 32
            or type(capability) is not str):
        raise ValueError("replay_envelope")
    allowed = {name: sys.modules[name] for name in (
        "dataclasses", "datetime", "html", "html.parser", "re", "urllib",
        "urllib.parse", "__future__")}
    sources = []
    total = 0
    for name, source in modules:
        if (type(name) is not str or re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]{0,127}", name) is None
                or name in sys.modules or name in allowed
                or type(source) is not str):
            raise ValueError("replay_module")
        total += len(source.encode("utf-8"))
        if total > 2_000_000:
            raise ValueError("replay_source_limit")
        module = types.ModuleType(name)
        allowed[name] = module
        sources.append((module, source))
    tape = []
    total = 0
    for method, url, status, content_type, encoded in entries:
        if (method not in ("GET", "HEAD") or type(url) is not str
                or type(status) is not int or not 100 <= status <= 599
                or type(content_type) is not str or len(content_type) > 1024):
            raise ValueError("replay_entry")
        body = base64.b64decode(encoded, validate=True)
        total += len(body)
        if total > 4 * 1024 * 1024:
            raise ValueError("replay_body_limit")
        match = re.search(r'(?i)(?:^|;)\s*charset\s*=\s*"?([a-z0-9_-]+)', content_type)
        charset = match.group(1).lower() if match else "utf-8"
        if charset not in ("utf-8", "us-ascii", "ascii", "iso-8859-1", "windows-1252"):
            raise ValueError("replay_encoding")
        tape.append((method, url, status, content_type, charset, body))

    def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in allowed:
            raise ImportError("replay_import_refused")
        return allowed[name] if fromlist else allowed[name.split(".")[0]]

    namespace = dict(vars(builtins))
    namespace["__import__"] = restricted_import
    cursor = 0
    failed = False

    def read(method, url):
        nonlocal cursor, failed
        if failed or cursor >= len(tape) or (method, url) != tape[cursor][:2]:
            failed = True
            raise ValueError("replay_unmatched_request")
        _, _, status, content_type, charset, body = tape[cursor]
        cursor += 1
        return types.SimpleNamespace(status_code=status, content=body,
                                     headers={"content-type": content_type}, encoding=charset)

    _confine()
    for module, source in sources:
        sys.modules[module.__name__] = module
        module.__dict__["__builtins__"] = namespace
        exec(compile(source, "<installed-replay-module>", "exec", dont_inherit=True), module.__dict__)
    parser = sources[-1][0].__dict__.get("parse_projection")
    if not callable(parser):
        raise ValueError("replay_entrypoint")
    rows = parser(capability, read)
    if failed or cursor != len(tape):
        raise ValueError("replay_incomplete")
    digest = projection_digest(rows)
    if digest is None:
        raise ValueError("replay_projection")
    return {"digest": digest, "rows": len(rows)}


def main() -> None:
    # Keep the wire descriptor private; plugin stdout/stderr cannot pollute
    # structured output even if it attempts print/os.write.
    protocol_fd = os.dup(1)
    try:
        replay = sys.argv[1:] == ["--provider-health-replay"]
        if sys.argv[1:] and not replay:
            raise ValueError("worker_mode")
        request_limit = MAX_REPLAY_REQUEST_BYTES if replay else MAX_REQUEST_BYTES
        raw = sys.stdin.buffer.read(request_limit + 1)
        if not raw or len(raw) > request_limit:
            raise ValueError("consolidator_request_unbounded")
        envelope = json.loads(raw)
        if type(envelope) is not dict:
            raise ValueError("invalid_consolidator_request")
        # No inherited env/credential is available when plugin code runs.
        os.environ.clear()
        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        # Bound address space as well as CPU: pure plugins may still allocate
        # unbounded strings/lists before the 16 KiB result check. This is
        # applied inside the isolated worker, never in the parent Core.
        memory_cap = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_cap, memory_cap))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024, 64 * 1024))
        with open(os.devnull, "wb", buffering=0) as null_output:
            os.dup2(null_output.fileno(), 1)
            os.dup2(null_output.fileno(), 2)
        result = _execute_replay(envelope) if replay else _execute(envelope)
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
