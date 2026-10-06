from types import SimpleNamespace
import pytest
import webui.device_manager as manager
from adb_connection import AdbAutoConnector


@pytest.fixture(autouse=True)
def isolated_connections(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, 'AUTO_CONNECTOR', AdbAutoConnector(tmp_path/'adb.json'))


def test_adb_text_reply_is_resolved_to_online_device(monkeypatch):
    monkeypatch.setattr(manager.adb, "connect", lambda address: "already connected to " + address)
    monkeypatch.setattr(manager, "get_device_by_serial", lambda address: SimpleNamespace(serial=address, get_state=lambda: "device"))
    result = manager.DeviceRuntimeManager.connect_network_device("127.0.0.1:16384")
    assert result["ok"] and result["serial"] == "127.0.0.1:16384"


def test_adb_failed_reply_does_not_report_success(monkeypatch):
    monkeypatch.setattr(manager.adb, "connect", lambda address: "failed to connect")
    def missing(address):
        raise ConnectionError("device not found")
    monkeypatch.setattr(manager, "get_device_by_serial", missing)
    result = manager.DeviceRuntimeManager.connect_network_device("127.0.0.1:16384")
    assert not result["ok"] and "device not found" in result["message"]


def test_offline_device_does_not_report_success(monkeypatch):
    monkeypatch.setattr(manager.adb, "connect", lambda address: "connected")
    monkeypatch.setattr(manager, "get_device_by_serial", lambda address: SimpleNamespace(serial=address, get_state=lambda: "offline"))
    assert not manager.DeviceRuntimeManager.connect_network_device("127.0.0.1:16384")["ok"]
