import time
from types import SimpleNamespace

import pytest
import scrcpy.core
from bot_instance import BotInstance, BotHalt
from window_controller import capture_options


def test_encoder_selection_reaches_android_server():
    commands=[]
    class Device:
        sync=SimpleNamespace(push=lambda *args:None)
        def shell(self, args, stream):
            commands.append(args)
            return SimpleNamespace(read=lambda size:b'[server]')
    client=scrcpy.core.Client(Device(),encoder_name='c2.android.avc.encoder')
    client._Client__deploy_server()
    assert 'video_encoder=c2.android.avc.encoder' in commands[0]


def test_capture_compatibility_settings_reach_client():
    params=capture_options({'scrcpy_encoder':'c2.android.avc.encoder',
                            'scrcpy_max_fps':30,'scrcpy_max_width':1280,'scrcpy_bitrate':2000000},60)
    assert params==dict(encoder_name='c2.android.avc.encoder',max_fps=30,max_width=1280,bitrate=2000000)


@pytest.mark.parametrize('state,player,enemy',[('match',0,600),('lobby',600,600),('respawn',600,600)])
def test_absent_enemy_or_non_match_never_restarts_game(state,player,enemy):
    bot=stub_bot(state,player,enemy)
    bot.manage_time_tasks(None)
    assert not bot.restarts and not bot.releases


def stub_bot(state,player,enemy):
    bot=BotInstance.__new__(BotInstance);now=time.time()
    bot.Time_management=SimpleNamespace(state_check=lambda:False,no_detections_check=lambda:True,idle_check=lambda:False)
    bot.Play=SimpleNamespace(time_since_detections={'player':now-player,'enemy':now-enemy})
    bot.no_detections_action_threshold=480;bot.get_latest_state=lambda:state
    bot.webhook_ping_every_minutes=0;bot.releases=[];bot.restarts=[]
    bot.window_controller=SimpleNamespace(release_all_inputs=lambda:bot.releases.append(True))
    bot.restart_brawl_stars=lambda:bot.restarts.append(True)
    return bot


def test_sustained_perception_failure_stops_without_killing_game():
    bot=stub_bot('match',600,600)
    with pytest.raises(BotHalt):bot.manage_time_tasks(None)
    assert bot.releases==[True] and not bot.restarts
