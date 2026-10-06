"""ADB handshakes with a finite timeout, including adbutils' transport open."""
from adbutils import AdbClient
import json
import os
from pathlib import Path
import re
import socket
import threading
import time


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
    # An offline console alias must never hide its online TCP transport.
    def rank(device):
        try:
            online = device.get_state() == 'device'
        except Exception:
            online = False
        return (not online, not device.serial.startswith('emulator-'))
    devices = sorted(devices, key=rank)
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


def tcp_address(value):
    """Validate a device endpoint; port 5037 belongs to the host ADB server."""
    value = str(value or '').strip()
    match = re.fullmatch(r'(localhost|[A-Za-z0-9][A-Za-z0-9_.-]*|\[[0-9a-fA-F:]+\]):(\d+)', value)
    if not match or not 1 <= int(match[2]) <= 65535:
        raise ValueError('Use a device address such as 127.0.0.1:16384.')
    if int(match[2]) == int(os.environ.get('ANDROID_ADB_SERVER_PORT', '5037')):
        raise ValueError('5037 is the ADB server port, not an emulator device port. For MuMu use its ADB address (usually 127.0.0.1:16384).')
    return f'{match[1]}:{int(match[2])}'


class AdbAutoConnector:
    """Reconnect remembered endpoints and discover a local emulator, without scans.

    A poll tries at most two listening endpoints, with a two-second ADB timeout
    and a 30-second retry interval. Explicit disconnection suppresses retries.
    No server reset, guest restart or input is involved.
    """
    LOCAL_PORTS = (16384, 16416, 7555, 62001, 5555)

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._attempted = {}

    def _file(self):
        if self.path is not None:
            return self.path
        from utils import resolve_runtime_path
        return resolve_runtime_path('adb_connections.json')

    def _read(self):
        try:
            state = json.loads(self._file().read_text('utf-8'))
            return {key: [tcp_address(v) for v in state.get(key, [])] for key in ('addresses', 'ignored')}
        except (OSError, ValueError, TypeError):
            return {'addresses': [], 'ignored': []}

    def remember(self, address, disconnected=False):
        address = tcp_address(address)
        with self._lock:
            state = self._read()
            for key in ('addresses', 'ignored'):
                state[key] = [a for a in state[key] if canonical_device_serial(a) != canonical_device_serial(address)]
            state['ignored' if disconnected else 'addresses'].insert(0, address)
            self._attempted.pop(address, None)
            path = self._file()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, indent=2), 'utf-8')
            os.replace(temporary, path)

    @staticmethod
    def listening(address):
        host, port = address.rsplit(':', 1)
        try:
            with socket.create_connection((host.strip('[]'), int(port)), timeout=.15):
                return True
        except OSError:
            return False

    def refresh(self, client, devices, preferred_port=None):
        if not self._refresh_lock.acquire(blocking=False):
            return devices
        try:
            state = self._read()
            ignored = {canonical_device_serial(a) for a in state['ignored']}
            online = set()
            for d in devices:
                try:
                    if d.get_state() == 'device':
                        online.add(canonical_device_serial(d.serial))
                except Exception:
                    pass
            candidates = list(state['addresses'])
            if not online and not ignored:
                if preferred_port:
                    try:
                        candidates.append(tcp_address(f'127.0.0.1:{int(preferred_port)}'))
                    except (ValueError, TypeError):
                        pass
                # Old per-device profiles also retain the user's MuMu address.
                from utils import resolve_runtime_path
                for profile in sorted(resolve_runtime_path('devices').glob('127.0.0.1-*')):
                    try:
                        candidates.append(tcp_address(profile.name.replace('127.0.0.1-', '127.0.0.1:', 1)))
                    except ValueError:
                        pass
                candidates.extend(f'127.0.0.1:{p}' for p in self.LOCAL_PORTS)
            tried = 0
            for address in dict.fromkeys(candidates):
                if canonical_device_serial(address) in ignored or canonical_device_serial(address) in online:
                    continue
                now = time.monotonic()
                if now - self._attempted.get(address, -float('inf')) < 30:
                    continue
                self._attempted[address] = now
                if not self.listening(address):
                    continue
                tried += 1
                try:
                    client.connect(address, timeout=2)
                    current = client.device_list()
                    if any(canonical_device_serial(d.serial) == canonical_device_serial(address) and d.get_state() == 'device' for d in current):
                        try:
                            self.remember(address)
                        except OSError:
                            pass  # A read-only settings folder must not hide a working device.
                        return current
                except Exception:
                    pass
                if tried >= 2:
                    break
            return devices
        finally:
            self._refresh_lock.release()


AUTO_CONNECTOR = AdbAutoConnector()
