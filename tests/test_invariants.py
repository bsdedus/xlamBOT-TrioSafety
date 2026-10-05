import os
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from device_lease import DeviceLease
from match_audit import _pid_alive
from navigation_safety import evaluate_gas_risk
from play import Play
import play
import state_finder
from stage_manager import StageManager


def make_play(monkeypatch):
    monkeypatch.setattr(play,'Detect',lambda *a,**kw:SimpleNamespace())
    w=SimpleNamespace(width=960,height=540,width_ratio=.5,height_ratio=.5,scale_factor=.5)
    p=Play('main','tile','close',w,'movement=(0,0)')
    p.frame=np.zeros((540,960,3),np.uint8)
    p.gas_mask=np.zeros((540,960),np.uint8);p.gas_mask[:,550:]=255
    p.gas_observed_at=time.time();p.gas_detection_ok=True
    p.current_brawler='gus'
    return p


@pytest.mark.parametrize('proposal',['hysteresis','unstuck','playstyle'])
def test_every_movement_proposal_passes_final_gas_veto(monkeypatch,proposal):
    p=make_play(monkeypatch)
    data={'player':[[450,220,510,295]],'enemy':[],'teammate':[],'wall':[],'bush':[]}
    p.get_movement=lambda:(-100,0) if proposal!='playstyle' else (100,0)
    if proposal=='hysteresis':p.last_movement=(50,0);p.last_movement_change_time=time.time()
    if proposal=='unstuck':p.unstuck_movement_if_needed=lambda *a:(50,0)
    final=p.loop('gus',data,time.time())
    assert not p.evaluate_gas_risk(final,data['player'][0])['blocked']
    assert p.safety_telemetry['override_reason']=='GAS_PREVENTION'


def test_unrecognized_mode_never_presses_start(monkeypatch):
    import stage_manager
    calls=[]
    manager=StageManager.__new__(StageManager);manager.runtime_control=None
    manager.window_controller=SimpleNamespace(screenshot=lambda:np.zeros((540,960,3),np.uint8),
        release_all_inputs=lambda:calls.append('release'),press=lambda *a:calls.append('press'))
    monkeypatch.setattr(stage_manager,'selected_showdown_mode',lambda _:None)
    manager.start_game()  # transient unknown waits with every input released
    assert calls==['release']


@pytest.mark.parametrize('mode',['solo_showdown','duo_showdown','trio_showdown'])
def test_mode_template_at_each_resolution(monkeypatch,mode):
    monkeypatch.setattr(state_finder,'is_in_lobby',lambda _:True)
    for width,height in [(1920,1080),(1280,720),(1600,896)]:
        f=np.zeros((height,width,3),np.uint8)
        template=state_finder.load_template(str(state_finder.resolve_project_path('images','gamemodes',mode+'_selected.png')),width,height)
        y=int(height*.85);x=int(width*.4)
        f[y:y+template.shape[0],x:x+template.shape[1]]=template
        assert state_finder.selected_showdown_mode(f)==mode


def test_device_lease_rejects_duplicate_and_releases():
    lease=DeviceLease('unit-test-isolated-device')
    try:
        with pytest.raises(RuntimeError):DeviceLease('unit-test-isolated-device')
    finally:lease.close()
    DeviceLease('unit-test-isolated-device').close()


def test_pid_probe_does_not_terminate_process():
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    try:
        assert _pid_alive(str(process.pid))
        assert process.poll() is None
    finally:process.terminate();process.wait()


def test_unknown_screen_needs_hud_and_distinct_confirmations(monkeypatch):
    p=make_play(monkeypatch);releases=[];states=[]
    w=p.window_controller
    w.press_coords={'attack':[1723,793]}
    w.frame_is_fresh=lambda *a:True
    w.release_all_inputs=lambda:releases.append('release')
    w.begin_gameplay_frame=lambda *a:True
    p.get_main_data=lambda _: {'player':[[450,220,510,295]], 'enemy':[], 'teammate':[]}
    p.get_tile_data=lambda *a: {}
    p.publish_debug_view=lambda *a:None
    p.Detect_gas.detect_objects=lambda *a,**kw: {'gas':[]}
    p.Detect_gas.last_inference_ms=1;p.Detect_main_info.last_inference_ms=1;p.Detect_tile_detector.last_inference_ms=1
    p.get_movement=lambda:(0,0)
    p.do_movement=lambda _:states.append('movement')
    main=SimpleNamespace(current_frame_time=10,get_latest_state=lambda:'unknown',
        set_latest_state=lambda *a:states.append('match'),
        Stage_manager=SimpleNamespace(trio_session_confirmed=True,do_state=lambda *a:None))
    frame=np.zeros((540,960,3),np.uint8)
    p.main(frame,'gus',main)
    assert not states and p._match_confirmations==0
    frame[360:432,830:900]=[255,0,0]
    main.current_frame_time=11
    for _ in range(3):p.main(frame,'gus',main)
    assert p._match_confirmations==1 and not states
    main.current_frame_time=12;p.main(frame,'gus',main)
    assert not states
    main.current_frame_time=13;p.main(frame,'gus',main)
    assert states==['match','movement']


def test_battle_attach_without_trio_confirmation_has_no_inference_or_input(monkeypatch):
    p=make_play(monkeypatch);calls=[]
    p.window_controller.frame_is_fresh=lambda *a:True
    p.window_controller.release_all_inputs=lambda:calls.append('release')
    p.get_main_data=lambda *a:pytest.fail('Unconfirmed mode must not enter gameplay')
    main=SimpleNamespace(current_frame_time=10,get_latest_state=lambda:'match',
                        Stage_manager=SimpleNamespace(trio_session_confirmed=False))
    p.main(p.frame,'gus',main)
    assert calls==['release'] and not p.world_state['mode_confirmed']


@pytest.mark.parametrize('key,value',[('gas_memory_ttl',0),('gas_detect_interval',1),
    ('gas_confidence',float('nan')),('gas_area_bottom',.1),('gas_reach',20),('gas_danger_exit',.9)])
def test_invalid_gas_settings_are_rejected(key,value):
    from gas_config import validate_gas_config
    with pytest.raises(ValueError):validate_gas_config({key:value})


def test_saved_world_replays_through_same_wall_gas_policy():
    from replay_navigation import replay
    world={'timestamp':1,'player_present':True,'frame_size':[540,960],
           'player':[[450,220,510,295]],'wall':[],'enemy':[],'teammate':[],
           'gas_boxes':[[550,160,960,540]],'gas_coverage':0,
           'movement':{'desired':[100,0]}}
    result=replay(world)
    assert result['status']=='REPLAYED' and result['override_reason']=='GAS_PREVENTION'
    assert result['replayed_final'][0]<=0


def test_evidence_clip_and_event_are_saved_without_claiming_verified_cause(tmp_path):
    import cv2,json
    from match_audit import Auditor
    a=Auditor.__new__(Auditor);a.evidence=tmp_path
    _,image=cv2.imencode('.jpg',np.zeros((100,160,3),np.uint8))
    event={'event':'DEATH_INFERRED','life_id':1,'frames':[(1,image.tobytes(),{}),(2,image.tobytes(),{})]}
    result=a.save_death(1,event)
    assert result['primary_cause']=='UNKNOWN' and not result['root_cause_verified']
    assert result['clip_status']=='WRITTEN'
    clip=cv2.VideoCapture(str(tmp_path/'death_001_01/pre_death.avi'))
    try:assert clip.read()[0]
    finally:clip.release()


@pytest.mark.parametrize('ability',['use_super','use_gadget'])
def test_auto_movement_abilities_do_not_bypass_gas_policy(monkeypatch,ability):
    p=make_play(monkeypatch);calls=[]
    p.window_controller.press=lambda key:calls.append(key)
    p.current_brawler='bull';p.gas_player_box=[450,220,510,295]
    getattr(p,ability)()
    assert not calls and p.ability_vetoes
