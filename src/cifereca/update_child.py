"""Bounded, opt-in JSONL progress from the new updater runtime (#925).

stderr is never forwarded. Older runtimes ignore the environment capability
and retain their ordinary human stderr; those lines are simply discarded.
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from typing import Callable

PROGRESS_ENV = "CIFERECA_UPDATE_PROGRESS_JSONL"
MAX_LINE_BYTES = 2048
MAX_STDOUT_BYTES = 1024 * 1024
MAX_EVENTS = 16384


class ChildOutputError(RuntimeError):
    pass


def run_plugin_child(command: list[str], *, timeout: float,
                     on_line: Callable[[bytes], None]) -> subprocess.CompletedProcess:
    """Drain both pipes concurrently, with bounded memory and a single deadline.

    Callbacks run on the caller thread. Never return/rollback while the direct
    child is alive; on POSIX its subprocess group is also stopped on failure.
    """
    env = dict(os.environ, **{PROGRESS_ENV: "1"})
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, start_new_session=(os.name == "posix"))
    events: queue.Queue[bytes] = queue.Queue(maxsize=128)
    stopped = threading.Event()
    overflow = threading.Event()
    read_error = threading.Event()
    stdout = bytearray()
    done = [threading.Event(), threading.Event()]

    def drain_stdout():
        try:
            while chunk := proc.stdout.read(4096):
                if len(stdout) + len(chunk) > MAX_STDOUT_BYTES:
                    overflow.set()
                elif not overflow.is_set():
                    stdout.extend(chunk)
        except OSError:
            read_error.set()
        finally:
            proc.stdout.close()
            done[0].set()

    def drain_stderr():
        pending = bytearray()
        oversized = False
        count = 0
        try:
            # read1 returns immediately on available pipe bytes, unlike read(n)
            # which can wait for n bytes and conceal a flushed stage transition.
            while chunk := proc.stderr.read1(4096):
                for part_index, part in enumerate(chunk.split(b"\n")):
                    if part_index:
                        if pending and not oversized and count < MAX_EVENTS:
                            count += 1
                            while not stopped.is_set():
                                try:
                                    events.put(bytes(pending), timeout=0.05)
                                    break
                                except queue.Full:
                                    pass
                        pending.clear()
                        oversized = False
                    if not oversized:
                        if len(pending) + len(part) <= MAX_LINE_BYTES:
                            pending.extend(part)
                        else:
                            pending.clear()
                            oversized = True
                if stopped.is_set():
                    break
            # An unterminated final record is not an attested JSONL event.
        except OSError:
            read_error.set()
        finally:
            proc.stderr.close()
            done[1].set()

    readers = [threading.Thread(target=drain_stdout, daemon=True),
               threading.Thread(target=drain_stderr, daemon=True)]
    deadline = time.monotonic() + timeout
    try:
        for reader in readers:
            reader.start()
        while True:
            if overflow.is_set():
                raise ChildOutputError("update_child_output_limit")
            if time.monotonic() >= deadline:
                raise TimeoutError("update_child_timeout")
            try:
                line = events.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                pass
            else:
                on_line(line)
            if proc.poll() is not None and all(flag.is_set() for flag in done) and events.empty():
                break
        if overflow.is_set() or read_error.is_set():
            raise ChildOutputError("update_child_output_unavailable")
        return subprocess.CompletedProcess(command, proc.wait(), stdout.decode("utf-8"), "")
    finally:
        stopped.set()
        # A pipe may still be owned by a descendant after the leader exits.
        if proc.poll() is None or not all(flag.is_set() for flag in done):
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                proc.kill()
        proc.wait()
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=0.25)
