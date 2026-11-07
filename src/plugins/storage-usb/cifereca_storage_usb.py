"""Generic mass-storage staging transport for a Core-owned USB sink."""
from __future__ import annotations

from typing import Callable


_CHUNK_SIZE = 1024 * 1024


class UsbMassStorageTarget:
    def write_usb_stage(self, payload: bytes, append: Callable[[bytes], None]) -> None:
        for offset in range(0, len(payload), _CHUNK_SIZE):
            append(payload[offset:offset + _CHUNK_SIZE])


class UsbMassStoragePlugin:
    def activate(self, context) -> UsbMassStorageTarget:
        return UsbMassStorageTarget()


PLUGIN = UsbMassStoragePlugin()
