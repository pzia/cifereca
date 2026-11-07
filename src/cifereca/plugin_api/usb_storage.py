"""Optional staging capability for the storage.target category."""
from __future__ import annotations

from typing import Callable, Protocol


USB_STAGE_CAPABILITY = "storage.usb.stage"


class UsbStorageStageWriter(Protocol):
    """A transport that stages bytes through a sink owned by the Core.

    The call is synchronous. Append the supplied immutable bytes in order
    and on the calling thread. The sink expires when the call returns and may
    raise on cancellation or I/O failure. No path, file descriptor, permission
    or publication receipt crosses this API.
    """

    def write_usb_stage(self, payload: bytes, append: Callable[[bytes], None]) -> None: ...


__all__ = ["USB_STAGE_CAPABILITY", "UsbStorageStageWriter"]
