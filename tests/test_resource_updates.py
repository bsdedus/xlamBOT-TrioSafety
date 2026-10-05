import hashlib
from pathlib import Path

import pytest

from webui.resource_updates import ResourceUpdater, allowed_asset, repository_name


def blob(content):
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


class Reply:
    def __init__(self, data=None, content=b'', code=200, headers=None):
        self.data = data
        self.content = content
        self.status_code = code
        self.headers = headers or {}

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP ' + str(self.status_code))


class Session:
    def __init__(self, files):
        self.headers = {}
        self.files = files
        self.generation = 'a'
        self.corrupt = None
        self.requests = []
        self.on_download = lambda: None

    def get(self, url, **kwargs):
        self.requests.append(url)
        revision = self.generation * 40
        if '/commits/' in url:
            return Reply({'sha': revision, 'commit': {'tree': {'sha': revision}}}, headers={'ETag': revision})
        if '/git/trees/' in url:
            return Reply({'sha': revision, 'tree': [
                {'path': name, 'type': 'blob', 'mode': '100644', 'sha': blob(content), 'size': len(content)}
                for name, content in self.files.items()]})
        self.on_download()
        assert '/'+revision+'/' in url
        name = url.split('/'+revision+'/', 1)[1]
        return Reply(content=b'corrupt' if name == self.corrupt else self.files[name])


def updater(tmp_path):
    project = tmp_path / 'project'
    (project / 'static/css').mkdir(parents=True)
    (project / 'static/css/site.css').write_bytes(b'local unpublished')
    session = Session({'static/css/site.css': b'remote old', 'static/js/site.js': b'old'})
    return ResourceUpdater(project, tmp_path / 'data', session), session


def check_now(updater):
    updater.next_check_at = 0
    return updater.check()


def test_first_connection_preserves_unpublished_local_assets(tmp_path):
    u, session = updater(tmp_path)
    assert check_now(u)['connected']
    assert u.asset('static/css/site.css') is None
    assert (u.project_root / 'static/css/site.css').read_bytes() == b'local unpublished'
    assert len(session.requests) == 2


def test_resources_switch_together_and_restore_after_restart(tmp_path):
    u, session = updater(tmp_path)
    check_now(u)
    session.generation = 'b'
    session.files = {'static/css/site.css': b'new css', 'static/js/site.js': b'new js'}
    # Every download still sees the old active snapshot, never a partial update.
    observations = []
    session.on_download = lambda: observations.append(u.asset('static/css/site.css'))
    assert not check_now(u)['last_error']
    assert observations == [None, None]
    assert u.asset('static/css/site.css').read_bytes() == b'new css'
    assert u.asset('static/js/site.js').read_bytes() == b'new js'
    restored = ResourceUpdater(u.project_root, tmp_path / 'data', session)
    assert restored.asset('static/js/site.js').read_bytes() == b'new js'


def test_bad_hash_keeps_last_complete_snapshot(tmp_path):
    u, session = updater(tmp_path)
    check_now(u)
    session.generation = 'b'
    session.files['static/css/site.css'] = b'first update'
    check_now(u)
    previous_revision = u.status()['revision']
    session.generation = 'c'
    session.files = {'static/css/site.css': b'second update', 'static/js/site.js': b'new js'}
    session.corrupt = 'static/js/site.js'
    assert 'целостности' in check_now(u)['last_error']
    assert u.status()['revision'] == previous_revision
    assert u.asset('static/css/site.css').read_bytes() == b'first update'


@pytest.mark.parametrize('name', ['../static/a.js', 'static/../cfg/a.js', 'static\\a.js', '/static/a.js', 'cfg/a.js', 'models/a.onnx', 'main.py', 'images/states/match.png'])
def test_non_website_paths_are_not_updated(name):
    assert not allowed_asset(name)


def test_repository_cannot_redirect_to_another_host(tmp_path):
    u, _ = updater(tmp_path)
    with pytest.raises(ValueError):
        u.configure({'repository': 'https://github.com.attacker.test/owner/repo'})
    assert repository_name('https://github.com/bsdedus/xlamBOT-TrioSafety.git') == 'bsdedus/xlamBOT-TrioSafety'


def test_rate_limit_backoff_prevents_repeated_checks(tmp_path):
    u, session = updater(tmp_path)
    session.get = lambda *a, **kw: Reply(code=403, headers={'X-RateLimit-Reset': '9999999999'})
    assert 'ограничил' in check_now(u)['last_error']
    u.request_check()
    assert u.next_check_at == 9999999999


def test_bundle_upgrade_does_not_restore_older_overlay(tmp_path):
    u, session = updater(tmp_path)
    check_now(u)
    session.generation = 'b'
    session.files['static/css/site.css'] = b'remote update'
    check_now(u)
    (u.project_root / 'static/css/site.css').write_bytes(b'new app release')
    restored = ResourceUpdater(u.project_root, tmp_path / 'data', session)
    assert restored.asset('static/css/site.css') is None
