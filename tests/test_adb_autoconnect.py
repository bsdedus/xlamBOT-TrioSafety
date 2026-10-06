from types import SimpleNamespace
import pytest
import adb_connection as connection
import utils


def device(serial='127.0.0.1:16384', state='device'):
    return SimpleNamespace(serial=serial, get_state=lambda:state)


@pytest.fixture
def connector(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, 'resolve_runtime_path', lambda *p:tmp_path.joinpath(*p))
    return connection.AdbAutoConnector(tmp_path/'connections.json')


def client_for(devices, calls):
    return SimpleNamespace(connect=lambda address, timeout:calls.append((address,timeout)), device_list=lambda:devices)


def test_empty_panel_connects_running_mumu_and_remembers(connector, monkeypatch):
    calls=[]; online=[device()]
    monkeypatch.setattr(connector,'listening',lambda a:a=='127.0.0.1:16384')
    assert connector.refresh(client_for(online,calls),[])==online
    assert calls==[('127.0.0.1:16384',2)]
    assert connector._read()['addresses']==['127.0.0.1:16384']


def test_offline_transport_can_reconnect(connector, monkeypatch):
    calls=[];online=[device()]
    monkeypatch.setattr(connector,'listening',lambda _:True)
    assert connector.refresh(client_for(online,calls),[device(state='offline')])==online
    assert calls


def test_remembered_phone_reconnects_alongside_usb(connector, monkeypatch):
    connector.remember('192.168.1.20:5555')
    calls=[];usb=device('usb-device');online=[usb,device('192.168.1.20:5555')]
    monkeypatch.setattr(connector,'listening',lambda _:True)
    assert connector.refresh(client_for(online,calls),[usb])==online
    assert calls==[('192.168.1.20:5555',2)]


def test_online_device_is_not_reconnected_or_scanned(connector, monkeypatch):
    connector.remember('127.0.0.1:16384')
    monkeypatch.setattr(connector,'listening',lambda _:pytest.fail('Unexpected TCP probe'))
    assert connector.refresh(SimpleNamespace(),[device()])[0].serial=='127.0.0.1:16384'


def test_disconnected_address_stays_disconnected_across_restart(connector, monkeypatch):
    connector.remember('127.0.0.1:16384',disconnected=True)
    fresh=connection.AdbAutoConnector(connector.path);calls=[]
    monkeypatch.setattr(fresh,'listening',lambda a:a=='127.0.0.1:16384')
    assert fresh.refresh(client_for([device()],calls),[])==[]
    assert calls==[]
    fresh.remember('127.0.0.1:16384')
    assert not fresh._read()['ignored']


def test_missing_emulator_is_backed_off(connector, monkeypatch):
    probes=[]
    monkeypatch.setattr(connector,'listening',lambda a:probes.append(a) or False)
    assert connector.refresh(SimpleNamespace(),[])==[]
    count=len(probes)
    connector.refresh(SimpleNamespace(),[])
    assert len(probes)==count


def test_at_most_two_adb_attempts_per_poll(connector, monkeypatch):
    calls=[]
    monkeypatch.setattr(connector,'listening',lambda _:True)
    connector.refresh(client_for([],calls),[])
    assert len(calls)==2 and all(t==2 for _,t in calls)


def test_server_port_never_used_as_device(connector, monkeypatch):
    probes=[]
    monkeypatch.setattr(connector,'listening',lambda a:probes.append(a) or False)
    connector.refresh(SimpleNamespace(),[],preferred_port=5037)
    assert '127.0.0.1:5037' not in probes
    with pytest.raises(ValueError,match='server port'):
        connection.tcp_address('127.0.0.1:5037')


@pytest.mark.parametrize('address',['','localhost','host:0','host:65536','host:5555 shell','host:5555\nextra'])
def test_invalid_device_addresses_rejected(address):
    with pytest.raises(ValueError):connection.tcp_address(address)


def test_concurrent_poll_does_not_repeat_connect(connector):
    connector._refresh_lock.acquire()
    try:assert connector.refresh(SimpleNamespace(),[])==[]
    finally:connector._refresh_lock.release()


def test_success_not_hidden_by_readonly_preferences(connector,monkeypatch):
    monkeypatch.setattr(connector,'listening',lambda _:True)
    def readonly(*args):raise PermissionError('Read only')
    monkeypatch.setattr(connector,'remember',readonly)
    online=[device()]
    assert connector.refresh(client_for(online,[]),[])==online


def test_disconnect_does_not_reconnect_emulator_through_another_port(connector,monkeypatch):
    connector.remember('127.0.0.1:16384',disconnected=True)
    monkeypatch.setattr(connector,'listening',lambda _:True)
    calls=[]
    assert connector.refresh(client_for([device('127.0.0.1:5555')],calls),[])==[]
    assert calls==[]


def test_online_tcp_alias_wins_over_offline_console():
    tcp=device('127.0.0.1:5555')
    assert connection.unique_devices([device('emulator-5554','offline'),tcp])==[tcp]


def test_online_console_alias_is_preferred_when_both_online():
    console=device('emulator-5554')
    assert connection.unique_devices([device('127.0.0.1:5555'),console])==[console]
