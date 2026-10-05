import importlib
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from detect import Detect
import detect
import state_finder
import utils
import device_profiles
from webui.app import create_app
from webui.device_manager import DeviceRuntimeManager


def test_preprocess_padding_does_not_retain_previous_frame():
    d=Detect.__new__(Detect);d.input_size=(640,640)
    d._padded_img_buffer=np.zeros((1,3,640,640),np.float32)
    d.preprocess_image(np.full((640,320,3),255,np.uint8))
    buffer,_,_=d.preprocess_image(np.zeros((320,640,3),np.uint8))
    assert np.allclose(buffer[:,:,320:,:],128/255)
    assert np.allclose(buffer[:,:,:320,:],0)


def test_gpu_creation_failure_falls_back_to_cpu(monkeypatch):
    attempts=[]
    monkeypatch.setattr(detect.ort,'get_available_providers',lambda:['CUDAExecutionProvider','DmlExecutionProvider','CPUExecutionProvider'])
    def session(path,sess_options,providers):
        attempts.append(providers[0])
        if providers[0]!='CPUExecutionProvider':raise RuntimeError('missing GPU runtime')
        return SimpleNamespace(get_providers=lambda:providers)
    monkeypatch.setattr(detect.ort,'InferenceSession',session)
    d=Detect.__new__(Detect);d.preferred_device='auto';d.optimal_threads_amount=2;d.model_path='model.onnx'
    _,provider=d.load_model()
    assert provider=='CPUExecutionProvider'
    assert attempts==['CUDAExecutionProvider','DmlExecutionProvider','CPUExecutionProvider']


def test_unrecognized_screen_is_unknown(monkeypatch):
    monkeypatch.setattr(state_finder,'is_template_in_region',lambda *a,**kw:False)
    assert state_finder.get_state(np.zeros((540,960,3),np.uint8))=='unknown'


def test_two_devices_do_not_write_shared_queue(tmp_path,monkeypatch):
    monkeypatch.setattr(utils,'DATA_ROOT',tmp_path)
    utils.cached_toml.clear()
    utils.save_brawler_data([])
    for key in ('one','two'):
        device_profiles.save_queue(key,[])
    assert (tmp_path/'devices/one/latest_brawler_data.json').exists()
    assert (tmp_path/'devices/two/latest_brawler_data.json').exists()


def test_fresh_profile_bootstrap_is_successful(tmp_path,monkeypatch):
    monkeypatch.setattr(utils,'DATA_ROOT',tmp_path)
    utils.cached_toml.clear()
    utils.initialize_user_data()
    app=create_app(None,False);client=app.test_client()
    response=client.get('/api/bootstrap',headers={'X-Xlam-UI-Token':app.config['UI_API_TOKEN']})
    assert response.status_code==200


def test_running_worker_cannot_be_taken_over(monkeypatch):
    manager=DeviceRuntimeManager()
    runtime=manager._runtime_for('one','one')
    runtime._thread=SimpleNamespace(is_alive=lambda:True);runtime._state='stopping'
    assert not manager.start('one','one')['ok']
    assert runtime._thread.is_alive()


def test_cached_config_cannot_be_mutated_across_readers():
    first=utils.load_toml_as_dict('cfg/bot_config.toml')
    first['gas_confidence']=-999
    assert utils.load_toml_as_dict('cfg/bot_config.toml')['gas_confidence']!=-999


def test_settings_basename_writes_device_profile_not_install_tree(tmp_path,monkeypatch):
    monkeypatch.setattr(utils,'DATA_ROOT',tmp_path)
    utils.cached_toml.clear()
    utils.initialize_user_data()
    device_profiles.update_settings('one','bot_config.toml',{'brawler_switch_after_games':3})
    assert device_profiles.read_settings('one')['bot_config']['brawler_switch_after_games']==3
    assert (tmp_path/'devices/one/cfg/bot_config.toml').exists()


def test_adb_timeout_covers_shell_open_handshake():
    import socket,time
    from adbutils import AdbDevice
    from adb_connection import BoundedAdbClient
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen()
    release=threading.Event()
    def silent_adb():
        connection,_=server.accept()
        with connection:release.wait(1)
    worker=threading.Thread(target=silent_adb,daemon=True);worker.start()
    started=time.monotonic()
    try:
        device=AdbDevice(BoundedAdbClient(port=server.getsockname()[1],operation_timeout=.08),transport_id=1)
        with pytest.raises(Exception):device.shell('echo test',timeout=.02)
        assert time.monotonic()-started<.5
    finally:
        release.set();worker.join(1);server.close()


@pytest.mark.parametrize('width,height',[(960,540),(1600,896),(1920,1080)])
def test_showdown_hud_is_positive_match_evidence_and_dimmed_modal_is_not(width,height):
    template=state_finder.load_template(str(utils.resolve_project_path('images','states','teams_remaining_ru.png')),width,height)
    frame=np.zeros((height,width,3),np.uint8)
    x,y=int(width*.0175),int(height*.036)
    frame[y:y+template.shape[0],x:x+template.shape[1]]=template
    assert state_finder.is_in_showdown_match(frame)
    assert not state_finder.is_in_showdown_match((frame*.5).astype(np.uint8))


def test_android14_focus_avoids_activity_top_query():
    from adb_connection import foreground_package
    calls=[]
    def shell(command,timeout):
        calls.append(command)
        return 'mCurrentFocus=null\nmCurrentFocus=Window{a1 u0 com.supercell.brawlstars/.GameApp}'
    assert foreground_package(SimpleNamespace(shell=shell))=='com.supercell.brawlstars'
    assert calls==[['dumpsys','window','displays']]
def test_emulator_aliases_share_exclusive_input_lease():
    from device_lease import DeviceLease
    from adb_connection import canonical_device_serial, unique_devices
    from types import SimpleNamespace
    import pytest
    assert canonical_device_serial('emulator-5554') == canonical_device_serial('127.0.0.1:5555')
    assert canonical_device_serial('emulator-5556') != canonical_device_serial('emulator-5554')
    devices = unique_devices([SimpleNamespace(serial='127.0.0.1:5555'), SimpleNamespace(serial='emulator-5554')])
    assert [d.serial for d in devices] == ['emulator-5554']
    lease = DeviceLease('emulator-6552')
    try:
        with pytest.raises(RuntimeError):
            DeviceLease('127.0.0.1:6553')
    finally:
        lease.close()


def test_offline_adb_does_not_restart_game_or_crash_runtime():
    from bot_instance import BotInstance
    from types import SimpleNamespace
    from adbutils import AdbError
    bot=BotInstance.__new__(BotInstance)
    bot.device_label='offline-test';bot.time_since_checked_if_brawl_stars_crashed=0
    bot.check_if_brawl_stars_crashed_timer=5
    calls=[]
    def unavailable():raise AdbError('transport unavailable')
    bot.window_controller=SimpleNamespace(brawl_stars_process_alive=unavailable,
        release_all_inputs=lambda:calls.append('UP'), reconnect_scrcpy=lambda **kw:False,
        launch_brawl_stars=lambda:calls.append('launch'))
    bot.Play=SimpleNamespace(world_state={'old':True},clear_gas_state=lambda:calls.append('clear'))
    bot.set_latest_state=lambda state:calls.append(state)
    assert bot.check_and_handle_brawl_stars_crash() is False
    assert bot.check_and_handle_brawl_stars_crash() is False
    assert 'launch' not in calls and 'UP' in calls and bot.Play.world_state=={}


def test_locked_history_write_has_finite_deadline(monkeypatch,tmp_path):
    import trophy_observer as observer
    import pytest
    clock=[0.]
    monkeypatch.setattr(observer.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(observer.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def locked(*args):raise PermissionError('locked')
    monkeypatch.setattr(observer.os,'replace',locked)
    with pytest.raises(PermissionError,match='remains locked'):
        observer.TrophyObserver._replace_when_available(tmp_path/'source',tmp_path/'target')
    assert clock[0]<5


def test_recognized_team_panel_closes_only_its_ui_button():
    from stage_manager import StageManager
    from types import SimpleNamespace
    calls=[]
    def click(x,y,delay=.02,already_include_ratio=True):calls.append((x,y,delay,already_include_ratio))
    stage=StageManager.__new__(StageManager)
    stage._should_stop=lambda:False;stage._should_pause=lambda:False
    stage.window_controller=SimpleNamespace(click=click)
    stage.close_team_panel()
    assert calls==[(1835,50,.15,False)]
    stage._should_stop=lambda:True
    stage.close_team_panel()
    assert len(calls)==1


@pytest.mark.parametrize('width,height',[(960,540),(1600,896),(1920,1080)])
@pytest.mark.parametrize('place,filename',[(1,'2nd_ru_2026.png'),(2,'3rd_ru_2026.png')])
def test_result_placement_ignores_animated_brawler(width,height,place,filename):
    template=state_finder.load_template(str(utils.resolve_project_path('images','end_results',filename)),width,height)
    rng=np.random.default_rng(42)
    frame=rng.integers(0,255,size=(height,width,3),dtype=np.uint8)
    x,y=int(width*48/1920),int(height*48/1080)
    frame[y:y+template.shape[0],x:x+template.shape[1]]=template
    assert state_finder.find_game_result(frame)==f'trio_showdown_{place}'
    assert state_finder.find_game_result(np.zeros_like(frame)) is False


def test_transient_lobby_animation_waits_for_positive_trio_without_taps(monkeypatch):
    import stage_manager as module
    stage=module.StageManager.__new__(module.StageManager)
    clock=[0.];modes=iter((None,None,'trio_showdown','solo_showdown'))
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module,'selected_showdown_mode',lambda frame:next(modes))
    released=[]
    stage.window_controller=SimpleNamespace(screenshot=lambda:object(),release_all_inputs=lambda:released.append(True))
    assert stage.require_trio_lobby() is False
    clock[0]=5;assert stage.require_trio_lobby() is False
    clock[0]=8;assert stage.require_trio_lobby() is True
    assert len(released)==2
    with pytest.raises(RuntimeError,match='solo_showdown'):stage.require_trio_lobby()


def test_unknown_lobby_confirmation_has_finite_deadline(monkeypatch):
    import stage_manager as module
    stage=module.StageManager.__new__(module.StageManager);clock=[0.]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(module,'selected_showdown_mode',lambda frame:None)
    stage.window_controller=SimpleNamespace(screenshot=lambda:object(),release_all_inputs=lambda:None)
    assert stage.require_trio_lobby() is False
    clock[0]=16
    with pytest.raises(RuntimeError,match='unknown'):stage.require_trio_lobby()
