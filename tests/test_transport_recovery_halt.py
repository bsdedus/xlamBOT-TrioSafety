from types import SimpleNamespace

import pytest

from bot_instance import BotHalt, BotInstance


def make_bot():
    bot = BotInstance.__new__(BotInstance)
    bot.releases = []
    bot.window_controller = SimpleNamespace(release_all_inputs=lambda: bot.releases.append(True))
    return bot


def test_offline_emulator_halts_instead_of_waiting_forever():
    bot = make_bot()
    bot.check_transport_recovery(False, now=0.0)
    bot.check_transport_recovery(False, now=59.0)
    with pytest.raises(BotHalt, match='60 секунд'):
        bot.check_transport_recovery(False, now=60.0)
    assert len(bot.releases) == 3


def test_fresh_frame_allows_a_later_independent_recovery():
    bot = make_bot()
    bot.check_transport_recovery(False, now=10.0)
    bot.check_transport_recovery(True, now=40.0)
    bot.check_transport_recovery(False, now=200.0)
    bot.check_transport_recovery(False, now=259.0)
    with pytest.raises(BotHalt):
        bot.check_transport_recovery(False, now=260.0)


def test_repeated_missing_frames_do_not_reset_recovery_deadline():
    bot = make_bot()
    for second in range(60):
        bot.check_transport_recovery(False, now=float(second))
    with pytest.raises(BotHalt):
        bot.check_transport_recovery(False, now=60.0)
