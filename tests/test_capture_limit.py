import pytest
import window_controller
from window_controller import WindowController, capture_fps_limit


@pytest.mark.parametrize('setting', ['auto', None, 0, '0', ''])
def test_automatic_capture_is_bounded(setting):
    assert capture_fps_limit(setting) == 60


def test_explicit_capture_limit_survives_client_recreation(monkeypatch):
    requests = []
    monkeypatch.setattr(window_controller.scrcpy, 'Client',
                        lambda **params: requests.append(params))
    controller = WindowController.__new__(WindowController)
    controller.device = object()
    controller.capture_options = window_controller.capture_options({}, 30)
    controller._create_scrcpy_client()
    controller._create_scrcpy_client()
    assert [item['max_fps'] for item in requests] == [30, 30]
    assert all(item['device'] is controller.device for item in requests)


def test_negative_capture_limit_rejected():
    with pytest.raises(ValueError):
        capture_fps_limit(-1)
