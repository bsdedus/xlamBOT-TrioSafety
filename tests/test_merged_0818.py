import io
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

import brawler_calibration as calibration
import device_profiles
import stage_manager
import training_capture
import trophy_reader
import utils
import window_controller
from device_lease import DeviceLease
from webui.app import create_app


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, 'DATA_ROOT', tmp_path)
    utils.cached_toml.clear()
    utils.initialize_user_data()
    yield tmp_path
    utils.cached_toml.clear()


def test_calibration_is_per_device_and_reset_keeps_other_buttons(isolated_data):
    device_profiles.update_settings('calibration-a', 'buttons_config', {'movement_joystick': [191, 901]})
    calibration.save('calibration-a', {'brawlers_card_00': [444, 222]}, {'account_total': [400, 20, 120, 50]}, (1280, 720))
    with device_profiles.use_profile('calibration-a'):
        assert calibration.region_for('account_total') == (400, 20, 120, 50)
        assert utils.load_toml_as_dict('cfg/buttons_config.toml')['brawlers_first_card'] == [444, 222]
    with device_profiles.use_profile('calibration-b'):
        assert calibration.region_for('account_total') is None
    calibration.reset('calibration-a')
    with device_profiles.use_profile('calibration-a'):
        assert calibration.region_for('account_total') is None
        assert utils.load_toml_as_dict('cfg/buttons_config.toml')['movement_joystick'] == [191, 901]


@pytest.mark.parametrize('value', [[True, 12], [float('nan'), 0], [-1, 0], [1920, 1], [1], '1,2'])
def test_invalid_calibration_is_rejected(value):
    with pytest.raises(ValueError):
        calibration.validate({'brawlers_menu': value}, ['brawlers_menu'])


@pytest.mark.parametrize('key', ['..', '.', '../profile', 'a/b', 'a\\b', ''])
def test_calibration_profile_traversal_is_rejected(key):
    with pytest.raises(ValueError):
        calibration.key_for(key)


@pytest.fixture
def local_ui(isolated_data, monkeypatch):
    app = create_app(None, False)
    manager = app.config['device_manager']
    manager.get_status = Mock(return_value={'is_running': False, 'state': 'idle'})
    manager.resolve_serial = Mock(return_value='unit-calibration-0818')
    device = SimpleNamespace(screenshot=Mock(return_value=Image.new('RGB', (1280, 720))), shell=Mock())
    monkeypatch.setattr(window_controller, 'get_device_by_serial', lambda serial: device)
    return app, app.test_client(), {'X-Xlam-UI-Token': app.config['UI_API_TOKEN']}, device


def test_new_pages_render_local_session(local_ui):
    app, client, headers, _ = local_ui
    for url in ['/', '/panel', '/training', '/calibration/calibration-a']:
        response = client.get(url)
        assert response.status_code == 200
        assert app.config['UI_API_TOKEN'].encode() in response.data
        assert b'js/ui-session.js' in response.data
    assert client.get('/api/training/sessions', headers=headers).json['sessions'] == []
    assert client.get('/api/brawler-calibration/calibration-a').status_code == 403


def test_calibration_requires_snapshot_and_tap_consumes_it(local_ui):
    app, client, headers, device = local_ui
    base = '/api/brawler-calibration/calibration-a'
    assert client.post(base, headers=headers, json={'points': {'brawlers_menu': [10, 20]}}).status_code == 400
    response = client.get(base+'/snapshot', headers=headers)
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).size == (1280, 720)
    frame = response.headers['X-Calibration-Frame']
    saved = client.post(base, headers=headers, json={'points': {'brawlers_menu': [960, 540]}, 'frame': frame})
    assert saved.status_code == 200
    payload = {'point': 'brawlers_menu', 'value': [960, 540], 'frame': frame}
    assert client.post(base+'/tap', headers=headers, json=payload).status_code == 200
    device.shell.assert_called_once_with(['input', 'tap', '640', '360'], timeout=5)
    assert client.post(base+'/tap', headers=headers, json=payload).status_code == 400
    assert device.shell.call_count == 1


@pytest.mark.parametrize('state', ['starting', 'running', 'paused', 'pausing', 'stopping'])
def test_calibration_never_captures_running_or_paused_bot(local_ui, state):
    app, client, headers, device = local_ui
    app.config['device_manager'].get_status.return_value = {'is_running': False, 'state': state}
    assert client.get('/api/brawler-calibration/calibration-a/snapshot', headers=headers).status_code == 400
    device.screenshot.assert_not_called()
    device.shell.assert_not_called()


def test_calibration_does_not_capture_device_owned_by_another_process(local_ui):
    app, client, headers, device = local_ui
    lease = DeviceLease('unit-calibration-0818')
    try:
        assert client.get('/api/brawler-calibration/calibration-a/snapshot', headers=headers).status_code >= 400
        device.screenshot.assert_not_called()
    finally:
        lease.close()


def test_calibration_changed_resolution_rejects_tap(local_ui):
    _, client, headers, device = local_ui
    base = '/api/brawler-calibration/calibration-a'
    frame = client.get(base+'/snapshot', headers=headers).headers['X-Calibration-Frame']
    device.screenshot.return_value = Image.new('RGB', (1600, 900))
    assert client.post(base+'/tap', headers=headers, json={'point':'brawlers_menu','value':[1,2],'frame':frame}).status_code == 400
    device.shell.assert_not_called()


def test_calibrated_account_total_accepts_zero(monkeypatch):
    monkeypatch.setattr(trophy_reader, 'OCR_AVAILABLE', True)
    monkeypatch.setattr(calibration, 'region_for', lambda kind: (1, 2, 30, 40))
    reader = Mock(return_value=0)
    monkeypatch.setattr(trophy_reader, '_read_region_digits', reader)
    assert trophy_reader.read_account_total(np.zeros((10,10,3),np.uint8)) == 0
    assert reader.call_args.kwargs == {'allow_zero': True}
    assert trophy_reader.card_offset(8) == (0, 0)


def test_training_counts_and_export_omit_excluded_images(isolated_data, monkeypatch):
    monkeypatch.setattr(training_capture, 'model_catalog', lambda: {'classes':['gas'], 'models':[]})
    session = training_capture.TrainingSession('calibration-a', 'test-session', training_capture.training_root()/'calibration-a/test-session')
    (session.folder/'images').mkdir(parents=True)
    for n in range(3):
        name = f'{n}.jpg'
        Image.new('RGB', (64,32), (n*70,0,0)).save(session.folder/'images'/name)
        session.add_frame(name,64,32)
        session.set_boxes(name,[],True,excluded=n==2)
    summary = session.summary()
    assert (summary['frames'], summary['included'], summary['excluded'], summary['checked']) == (3,2,1,3)
    assert session.folder.is_relative_to(isolated_data)
    with zipfile.ZipFile(session.export_zip()) as archive:
        images = [n for n in archive.namelist() if n.endswith('.jpg')]
        assert len(images) == 2
        assert not any(n.endswith('2.jpg') for n in images)


def test_end_result_updates_trophies_exactly_once(monkeypatch):
    manager = stage_manager.StageManager.__new__(stage_manager.StageManager)
    controller = SimpleNamespace(screenshot=Mock(return_value=np.zeros((10,10,3),np.uint8)), press=Mock(), release_all_inputs=Mock())
    observer = SimpleNamespace(parse_game_result=Mock(return_value=SimpleNamespace(result='defeat')), add_trophies=Mock(), add_win=Mock(), current_trophies=125, current_wins=1, win_streak=0)
    manager.window_controller, manager.Trophy_observer = controller, observer
    manager.brawlers_pick_data = [{'brawler':'shelly','type':'trophies'}]
    manager.current_brawler = lambda: 'shelly'
    manager._should_stop = manager._should_pause = lambda: False
    manager._sleep_interruptible = lambda *a: False
    manager.time_since_last_stat_change = 0
    manager.playstyle_info = {}
    manager.play_again_on_win = False
    monkeypatch.setattr(stage_manager, 'get_state', Mock(side_effect=['end_defeat','lobby']))
    monkeypatch.setattr(stage_manager, 'is_underdog', lambda frame: False)
    monkeypatch.setattr(stage_manager, 'save_brawler_data', Mock())
    monkeypatch.setattr(trophy_reader, 'read_result_delta', lambda frame: 5)
    manager.end_game()
    observer.add_trophies.assert_called_once()
    assert observer.add_trophies.call_args.kwargs['observed_delta'] == 5
    observer.add_win.assert_called_once()
