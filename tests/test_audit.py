import numpy as np
import cv2
from types import SimpleNamespace
from life_tracking import LifeTracker


def test_unknown_state_keeps_full_clip_history_without_confirming_death():
    tracker=LifeTracker(history_seconds=20)
    for stamp in range(3):tracker.update(stamp,True,'match',b'frame',{'player_present':True})
    for stamp in range(3,22):
        assert tracker.update(stamp,False,'unknown',b'frame',{'state':'unknown'})==[]
    events=tracker.update(22,False,'match',b'frame',{'respawn_ui':True})
    assert events[0]['event']=='DEATH_CONFIRMED'
    assert len(events[0]['frames'])==21
    assert events[0]['frames'][-1][0]-events[0]['frames'][0][0]==20
from match_audit import Auditor


def spawn(t):
    for stamp in (0,1,2):t.update(stamp,True,'match',b'frame',{'player_present':True})


def test_short_detection_flicker_is_not_death():
    t=LifeTracker();spawn(t)
    t.update(3,False,'match');t.update(4,True,'match');t.update(5,True,'match');events=t.update(6,True,'match')
    assert not events and t.life_id==1


def test_each_respawn_and_final_death_is_retained():
    t=LifeTracker();spawn(t)
    t.update(3,False,'match')
    for stamp in (9,10,11):events=t.update(stamp,True,'match')
    assert events[0]['event']=='DEATH_INFERRED' and events[0]['life_id']==1
    assert t.life_id==2
    t.update(12,False,'match');events=t.finish(18)
    assert events[0]['life_id']==2


def test_repeated_frame_and_unknown_state_do_not_invent_death():
    t=LifeTracker();spawn(t)
    for _ in range(10):t.update(3,False,'unknown')
    assert not t.finish(20)


def test_auditor_gas_region_matches_runtime_algorithm():
    a=Auditor.__new__(Auditor)
    a.gas=SimpleNamespace(detect_objects=lambda image,conf_tresh:{'gas':[[0,0,100,15],[10,80,50,100]]})
    mask=a._gas_mask(np.zeros((100,100,3),np.uint8))
    assert not mask[:20].any() and mask[90].any()


def test_auditor_decodes_evidence():
    a=Auditor.__new__(Auditor)
    ok,data=cv2.imencode('.jpg',np.zeros((50,50,3),np.uint8))
    assert a._decode(data.tobytes()).shape==(50,50,3)


def test_result_transition_does_not_erase_observed_final_absence():
    t=LifeTracker();spawn(t);t.update(3,False,'match')
    t.update(9,False,'end_trio_showdown_3')
    assert t.finish(10)[0]['life_id']==1


def test_victory_transition_disappearance_is_not_a_death():
    tracker=LifeTracker();spawn(tracker)
    tracker.update(3,False,'match')
    tracker.update(4,False,'end_trio_showdown_0')
    assert not tracker.finish(4)


def test_positive_respawn_ui_records_each_death_once():
    tracker=LifeTracker();spawn(tracker)
    events=tracker.update(3,False,'match',world={'respawn_ui':True})
    assert events[0]['event']=='DEATH_CONFIRMED' and events[0]['verified']
    assert not tracker.update(4,False,'match',world={'respawn_ui':True})
    for stamp in (15,16,17):events=tracker.update(stamp,True,'match')
    assert events[0]['event']=='RESPAWN_CONFIRMED' and tracker.life_id==2


def test_same_respawn_countdown_with_false_player_is_not_a_second_death():
    tracker=LifeTracker();spawn(tracker)
    assert tracker.update(3,False,'match',world={'respawn_ui':True})[0]['event']=='DEATH_CONFIRMED'
    for stamp in (4,5,6,7,8,9,10):assert tracker.update(stamp,True,'match')==[]
    assert tracker.update(11,False,'match',world={'respawn_ui':True})==[]
    assert tracker.life_id==1 and len(tracker.events)==2


def test_result_delta_requires_signed_consensus(monkeypatch):
    import trophy_reader
    monkeypatch.setattr(trophy_reader,'OCR_AVAILABLE',True)
    answers=iter(['+11','+11','+11','+1','11','+11'])
    monkeypatch.setattr(trophy_reader.pytesseract,'image_to_string',lambda *a,**kw:next(answers))
    frame=np.zeros((1080,1920,3),np.uint8)
    assert trophy_reader.read_result_delta(frame)==11
    assert trophy_reader.read_result_delta(frame) is None
    assert trophy_reader.read_result_delta(frame) is None


def test_current_result_ocr_cannot_be_overwritten_by_previous_match_log(monkeypatch,tmp_path):
    import match_audit as audit
    import trophy_reader
    from types import SimpleNamespace
    import json
    clock=[100.]
    monkeypatch.setattr(audit.time,'time',lambda:clock[0])
    monkeypatch.setattr(audit.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(trophy_reader,'read_result_delta',lambda frame:5)
    monkeypatch.setattr(audit,'MATCH_END_GRACE_S',1)
    monkeypatch.setattr(audit,'MIN_MATCH_SECONDS',0)
    class FakePanel:
        count=0
        def __init__(self,*a):pass
        def telemetry(self):
            self.count+=1
            return {'detected_state':'match' if self.count<4 else 'end_trio_showdown_2',
                    'world':{},'gas':{}}
        def snapshot(self):return b'frame'
        def logs(self):return ['Observed result trophies: +11']
    class FakeAuditor:
        def __init__(self,*a):pass
        def _decode(self,data):return np.zeros((1080,1920,3),np.uint8)
        def analyse(self,*a,**kw):return {'cause':'unknown','peak_gas_share':0,'clearest_side_share':1,
                                        'closest_enemy_share':None,'frames_examined':3}
    monkeypatch.setattr(audit,'Panel',FakePanel)
    monkeypatch.setattr(audit,'Auditor',FakeAuditor)
    args=SimpleNamespace(panel='local',serial='fake',out=str(tmp_path/'matches.jsonl'),buffer=30,
                         target=1,interval=.5,analyse_frames=30)
    assert audit.run(args,tmp_path/'lock')==0
    result=json.loads((tmp_path/'matches.jsonl').read_text(encoding='utf-8'))
    assert result['place']==3 and result['delta']==5 and result['delta_source']=='result_ocr'


def test_result_counter_adds_confirmed_but_unlocalized_final_death(monkeypatch,tmp_path):
    import trophy_reader
    monkeypatch.setattr(trophy_reader,'OCR_AVAILABLE',True)
    answers=iter(['3','3','3','2'])
    monkeypatch.setattr(trophy_reader,'_read_region_text',lambda *a:next(answers))
    frame=np.zeros((1080,1920,3),np.uint8)
    assert trophy_reader.read_result_death_count(frame)==3
    assert trophy_reader.read_result_death_count(frame) is None
