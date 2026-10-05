import math
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest

from navigation_safety import GasMemory, MovementArbiter, evaluate_gas_risk, gas_boxes_mask
from play import Play
from window_controller import WindowController


def choose(mask, desired, wall=lambda m,d:False, escape=False, center=(480,270), **extra):
    return MovementArbiter().choose(desired, mask=mask, center=center, radius=26.5,
                                    walls_block=wall, frame_size=(960,540), tile=27,
                                    escape=escape, **extra)


@pytest.mark.parametrize('angle',range(0,360,45))
def test_dangerous_corridor_cannot_beat_safe_walkable_alternative(angle):
    mask=np.zeros((540,960),np.uint8)
    dx,dy=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    for along in range(40,220):
        x,y=int(480+dx*along),int(270+dy*along)
        mask[y-18:y+18,x-18:x+18]=255
    final,reason,options,requested=choose(mask,(dx*100,dy*100))
    assert requested['blocked']
    assert reason=='GAS_PREVENTION'
    assert not evaluate_gas_risk(mask,(480,270),26.5,final).blocked


def test_thin_strip_between_old_samples_is_not_missed():
    mask=np.zeros((540,960),np.uint8);mask[:,550:552]=255
    assert evaluate_gas_risk(mask,(480,270),26.5,(1,0),sensitivity=.02).blocked


def test_safety_override_preserves_physical_joystick_amplitude():
    mask=np.zeros((540,960),np.uint8);mask[:,530:]=255
    final,reason,*_=choose(mask,(83.,0.))
    assert reason=='GAS_PREVENTION'
    assert math.hypot(*final)==pytest.approx(83.)
    final,reason,*_=choose(mask,(0.,0.),escape=True,fallback_magnitude=83.)
    assert math.hypot(*final)==pytest.approx(83.)


def test_wall_covering_only_clean_exit_does_not_get_selected():
    mask=np.full((540,960),255,np.uint8);mask[240:300,470:960]=0
    final,reason,options,_=choose(mask,(1,0),wall=lambda m,d:m[0]>.5,escape=True)
    assert final[0]<=.5
    assert reason=='GAS_ESCAPE'


def test_all_walls_hold_instead_of_first_bad_option():
    final,reason,*_=choose(np.zeros((540,960),np.uint8),(100,0),wall=lambda m,d:True)
    assert final==(0,0) and reason=='NO_WALKABLE_EXIT'


def test_center_enemy_cannot_override_gas_veto():
    mask=np.zeros((540,960),np.uint8);mask[:,530:]=255
    final,reason,*_=choose(mask,(100,0),enemies=[(420,270)],teammates=[(650,270)])
    assert final[0]<=0 and reason=='GAS_PREVENTION'


def test_unknown_frame_boundary_is_not_free_exit():
    final,reason,*_=choose(np.zeros((540,960),np.uint8),(-100,0),center=(35,270))
    assert final[0]>=0


def test_escape_overrides_chase_even_if_chase_is_clean():
    mask=np.zeros((540,960),np.uint8);mask[:,0:490]=255
    final,reason,*_=choose(mask,(-100,0),escape=True)
    assert final[0]>0 and reason=='GAS_ESCAPE'


def test_temporal_memory_expires_without_renewal_on_missing_cloud():
    m=GasMemory(.6);image=np.zeros((100,200,3),np.uint8)
    _,first=m.update(image,[[0,40,100,90]],10,0.21,1)
    _,retained=m.update(image,[],10.2,.21,1)
    _,expired=m.update(image,[],10.7,.21,1)
    assert first.any() and retained.any() and not expired.any()


def test_crop_excludes_top_not_bottom_and_clamps_outside_boxes():
    boxes,mask=gas_boxes_mask(np.zeros((100,200,3),np.uint8),
                              [[0,0,200,20],[20,80,100,120],[300,30,400,80]])
    assert boxes==[[20,80,100,100]]
    assert not mask[:20].any() and mask[90].any()


@pytest.mark.parametrize('scale',[1,2/3,5/6])
def test_resolution_scales_gas_risk(scale):
    width,height=int(1920*scale),int(1080*scale)
    image=np.zeros((height,width,3),np.uint8)
    _,mask=gas_boxes_mask(image,[[1100*scale,0,width,height]],0,1)
    risk=evaluate_gas_risk(mask,(960*scale,540*scale),53*scale,(100,0))
    assert risk.blocked and 0<=risk.risk<=1


@pytest.mark.parametrize('value',[(math.nan,0),(math.inf,1),('bad',1),None])
def test_invalid_movement(value):
    assert Play.movement_to_vector(value) is None


def fake_controller():
    w=WindowController.__new__(WindowController)
    w.serial='test-device';w.input_lock=threading.RLock();w.frame_lock=threading.Lock()
    w.active_touches={};w.input_enabled=True;w.gameplay_frame_time=None
    w.FRAME_STALE_TIMEOUT=.75;w.last_frame_time=time.time();w.last_frame=np.zeros((10,10,3))
    w.are_we_moving=False;w.last_joystick_pos=(None,None)
    w.PID_JOYSTICK=1;w.PID_ATTACK=2
    w.calls=[]
    w.scrcpy_client=SimpleNamespace(control=SimpleNamespace(touch=lambda *a:w.calls.append(a)))
    return w


def test_stale_frame_cannot_send_new_movement_and_releases_attack():
    w=fake_controller();w.touch_down(1,1,2);w.last_frame_time=time.time()-1
    with pytest.raises(ConnectionError):w.touch_move(2,2,1)
    assert len(w.calls)==2 and w.calls[-1][2]==1  # ACTION_UP
    assert not w.active_touches


def test_scene_expires_during_inference_even_if_stream_has_newer_frames():
    w=fake_controller();w.gameplay_frame_time=time.time()-1
    with pytest.raises(ConnectionError):w.touch_down(1,1,1)
    assert not w.calls


def test_touch_failure_releases_every_pointer_without_retry():
    w=fake_controller();w.touch_down(1,1,1);w.touch_down(2,2,2)
    def fail_move(x,y,action,pointer):
        if action==2:raise OSError('broken socket')
        w.calls.append((x,y,action,pointer))
    w.scrcpy_client.control.touch=fail_move
    with pytest.raises(ConnectionError):w.touch_move(3,3,1)
    assert not w.active_touches and len(w.calls)==4


def test_zero_joystick_vector_releases():
    w=fake_controller();w.are_we_moving=True;w.movement_joystick_x=1;w.movement_joystick_y=1
    w.touch_down(1,1,1);w.move(0,0)
    assert not w.are_we_moving and not w.active_touches


def test_wall_geometry_allows_departure_but_blocks_approach():
    wall=[[100,100,200,200]]
    assert Play.walls_block_swept_circle((50,150),(90,150),20,wall)
    assert not Play.walls_block_swept_circle((90,150),(50,150),20,wall)


def test_escape_cannot_step_into_cropped_out_gas_observation_area():
    mask=np.zeros((540,960),np.uint8)
    mask[113:170,450:510]=255
    final,reason,options,requested=choose(mask,(0,-83),escape=True,center=(480,145),observed_y=(113,540))
    assert requested['observation_boundary'] and requested['wall_collision']
    assert final[1]>=0 and math.hypot(*final)>0 and reason=='GAS_ESCAPE'


def test_unobserved_escape_endpoint_is_not_scored_as_clear():
    mask=np.zeros((540,960),np.uint8)
    mask[230:310,440:520]=255
    final,reason,options,requested=choose(mask,(0,-83),escape=True,observed_y=(113,540))
    assert not requested['terminal_observed'] and requested['terminal']==1
    assert not (abs(final[0])<1e-6 and final[1]<0)
