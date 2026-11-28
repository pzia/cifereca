import pytest
from types import SimpleNamespace as NS
from cifereca.core.process_liveness import _windows_dead

@pytest.mark.parametrize("handle,wait,dead", [(0,0,False),(42,0,True),(42,258,False),(42,0xffffffff,False)])
def test_windows_wait_is_non_destructive(handle, wait, dead):
    closed = []
    def open_process(access, inherit, pid):
        assert (access, inherit, pid) == (0x00100000, False, 123)
        return handle
    kernel = NS(OpenProcess=open_process, WaitForSingleObject=lambda h,t: wait,
                CloseHandle=lambda h: closed.append(h))
    assert _windows_dead(123,kernel) is dead
    assert closed == ([handle] if handle else [])
