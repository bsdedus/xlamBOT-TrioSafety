"""ADB handshakes with a finite timeout, including adbutils' transport open."""
from adbutils import AdbClient
import re


def canonical_device_serial(serial):
    """Share an input lease across the emulator's console/loopback ADB aliases."""
    serial = str(serial).strip()
    emulator = re.fullmatch(r'emulator-(\d+)', serial)
    if emulator:
        return 'local-adb:' + str(int(emulator.group(1)) + 1)
    local = re.fullmatch(r'(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)', serial)
    if local:
        return 'local-adb:' + str(int(local.group(1)))
    return serial


def unique_devices(devices):
    # Prefer the stable console serial when both aliases are reported online.
    devices = sorted(devices, key=lambda d: not d.serial.startswith('emulator-'))
    seen = set()
    out = []
    for device in devices:
        key = canonical_device_serial(device.serial)
        if key not in seen:
            seen.add(key)
            out.append(device)
    return out


def foreground_package(device):
    """Read current focus without adbutils' slow `dumpsys activity top`."""
    for section in ('displays','windows'):
        text=device.shell(['dumpsys','window',section],timeout=2)
        matches=re.findall(r'mCurrentFocus=Window\{[^\n]*?\s([^\s/]+)/[^\s}]+',text)
        if matches:return matches[-1]
    text=device.shell(['dumpsys','activity','activities'],timeout=2)
    found=re.search(r'(?:topResumedActivity|mResumedActivity|ResumedActivity)[=:][^\n]*?\s([^\s/]+)/[^\s}]+',text)
    if found:return found.group(1)
    raise ConnectionError('ADB returned no foreground activity')


class BoundedAdbClient(AdbClient):
    def __init__(self, host=None, port=None, operation_timeout=5):
        self.operation_timeout = float(operation_timeout)
        super().__init__(host=host, port=port, socket_timeout=self.operation_timeout)

    def make_connection(self, timeout=None):
        # BaseDevice passes 600 seconds explicitly, overriding socket_timeout.
        limit = self.operation_timeout if timeout is None else min(float(timeout), self.operation_timeout)
        return super().make_connection(timeout=limit)
