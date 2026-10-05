"""OS-owned capture/input lease; released by the OS if a process crashes."""
import hashlib
import os
import tempfile
from pathlib import Path


class DeviceLease:
    def __init__(self, serial):
        from adb_connection import canonical_device_serial
        name=hashlib.sha256(canonical_device_serial(serial).encode()).hexdigest()[:24]
        self.handle=None
        self.file=None
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype=wintypes.HANDLE
            self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=self.kernel.CreateMutexW(None,False,'Local\\xlamBOT-device-'+name)
            if not handle:raise OSError(ctypes.get_last_error(),'Device mutex failed')
            if ctypes.get_last_error()==183:
                self.kernel.CloseHandle(handle)
                raise RuntimeError(f'Device {serial} already has a scrcpy owner')
            self.handle=handle
        else:
            import fcntl
            self.file=(Path(tempfile.gettempdir())/('xlambot-device-'+name+'.lock')).open('a+b')
            try:fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                self.file.close();self.file=None
                raise RuntimeError(f'Device {serial} already has a scrcpy owner')

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle);self.handle=None
        if self.file is not None:
            self.file.close();self.file=None
