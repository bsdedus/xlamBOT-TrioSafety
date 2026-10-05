"""Update website assets from a public GitHub branch as one complete snapshot."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import requests


DEFAULT_REPOSITORY = 'https://github.com/bsdedus/xlamBOT-TrioSafety'
CHECK_INTERVAL = 120
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def repository_name(url):
    parsed = urlsplit(str(url).strip())
    parts = parsed.path.strip('/').removesuffix('.git').split('/')
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or
            parsed.query or parsed.fragment or len(parts) != 2 or
            any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in ('.', '..') for part in parts)):
        raise ValueError('Нужна ссылка вида https://github.com/владелец/репозиторий')
    return '/'.join(parts)


def allowed_asset(name):
    if not isinstance(name, str) or '\\' in name or ':' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        return False
    if name == 'images/xlambot_logo.png':
        return True
    suffix = path.suffix.lower()
    if name.startswith('static/'):
        return suffix in {'.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.webp', '.gif', '.ico', '.woff', '.woff2'}
    return name.startswith(('api/assets/brawler_icons/', 'api/assets/brawler_icons2/')) and suffix == '.png'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


class ResourceUpdater:
    def __init__(self, project_root, data_root, session=None):
        self.project_root = Path(project_root)
        self.root = Path(data_root) / 'web_resources'
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'xlamBOT-resource-updater', 'Accept': 'application/vnd.github+json'})
        self.lock = threading.RLock()
        self.check_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.config = self._load('config.json', {'enabled': True, 'repository': DEFAULT_REPOSITORY, 'branch': 'main'})
        self.state = self._load('state.json', {})
        self.busy = False
        self.error = ''
        self.checked_at = None
        self.next_check_at = 0
        self.bundle_revision = self._bundle_revision()
        self.active = None
        if self.state.get('bundle_revision') != self.bundle_revision:
            self.state.pop('revision', None)
            self.state.pop('updated_at', None)
        self._restore_active()

    def _load(self, name, fallback):
        try:
            value = json.loads((self.root / name).read_text(encoding='utf8'))
            return value if isinstance(value, dict) else fallback
        except (ValueError, OSError):
            return fallback

    def _bundle_revision(self):
        digest = hashlib.sha256()
        for folder in ('static',):
            for path in sorted((self.project_root / folder).rglob('*')):
                if path.is_file():
                    digest.update(path.relative_to(self.project_root).as_posix().encode())
                    digest.update(path.read_bytes())
        return digest.hexdigest()[:20]

    def _restore_active(self):
        revision = self.state.get('revision', '')
        if (self.state.get('source') == self._source() and re.fullmatch(r'[a-f0-9]{40}', revision)
                and (self.root / 'versions' / revision / 'complete.json').is_file()):
            self.active = self.root / 'versions' / revision

    def _source(self):
        return self.config['repository'] + '#' + self.config['branch']

    def status(self):
        with self.lock:
            return {'ok': True, **self.config, 'interval_seconds': CHECK_INTERVAL,
                    'revision': self.state.get('revision') if self.active else self.bundle_revision,
                    'connected': self.state.get('source') == self._source() and 'files' in self.state,
                    'checking': self.busy, 'last_checked_at': self.checked_at,
                    'last_updated_at': self.state.get('updated_at'), 'last_error': self.error}

    def configure(self, payload):
        repo = repository_name(payload.get('repository', ''))
        branch = str(payload.get('branch') or 'main').strip()
        if not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}', branch) or '..' in branch or branch.startswith('/'):
            raise ValueError('Некорректное имя ветки GitHub')
        enabled = payload.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('enabled должен быть true или false')
        with self.check_lock, self.lock:
            old_source = self._source()
            self.config = {'repository': 'https://github.com/' + repo, 'branch': branch, 'enabled': enabled}
            write_json(self.root / 'config.json', self.config)
            if self._source() != old_source:
                self.state = {}
                self.active = None
                write_json(self.root / 'state.json', self.state)
            self.error = ''
            self.next_check_at = 0
        self.wake.set()
        return self.status()

    def asset(self, name):
        if not allowed_asset(name):
            return None
        with self.lock:
            active = self.active
        if active:
            target = active / name
            if target.is_file() and target.resolve().is_relative_to(active.resolve()):
                return target
        return None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='github-web-resources', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.wake.set()

    def request_check(self):
        # User checks may run sooner, but cannot bypass server rate-limit backoff.
        with self.lock:
            if not self.error and time.time() - (self.checked_at or 0) >= 10:
                self.next_check_at = 0
        self.wake.set()
        return self.status()

    def _run(self):
        while not self.stopped.is_set():
            self.check()
            self.wake.wait(CHECK_INTERVAL)
            self.wake.clear()

    def check(self):
        if not self.check_lock.acquire(blocking=False):
            return self.status()
        try:
            with self.lock:
                if not self.config['enabled'] or time.time() < self.next_check_at:
                    return self.status()
                self.busy = True
            self._check_snapshot()
            self.error = ''
        except Exception as error:
            self.error = str(error)
        finally:
            with self.lock:
                self.busy = False
                self.checked_at = time.time()
                self.next_check_at = max(self.next_check_at, self.checked_at + CHECK_INTERVAL)
            self.check_lock.release()
        return self.status()

    def _check_snapshot(self):
        source = self._source()
        repo = repository_name(self.config['repository'])
        headers = {}
        if self.state.get('source') == source and self.state.get('etag'):
            headers['If-None-Match'] = self.state['etag']
        url = f'https://api.github.com/repos/{repo}/commits/{quote(self.config["branch"], safe="")}'
        response = self.session.get(url, headers=headers, timeout=(3, 12))
        if response.status_code == 304:
            return
        if response.status_code in (403, 429):
            try:
                self.next_check_at = max(time.time() + float(response.headers.get('Retry-After', 120)),
                                         float(response.headers.get('X-RateLimit-Reset', 0)))
            except ValueError:
                pass
            raise RuntimeError('GitHub ограничил запросы; следующая проверка отложена')
        response.raise_for_status()
        commit = response.json()
        commit_sha = commit.get('sha', '')
        tree_sha = commit.get('commit', {}).get('tree', {}).get('sha', '')
        if not re.fullmatch(r'[a-f0-9]{40}', commit_sha) or not re.fullmatch(r'[a-f0-9]{40}', tree_sha):
            raise ValueError('GitHub вернул некорректную версию ветки')
        tree_response = self.session.get(f'https://api.github.com/repos/{repo}/git/trees/{tree_sha}?recursive=1', timeout=(3, 12))
        tree_response.raise_for_status()
        tree = tree_response.json()
        if tree.get('truncated') or not re.fullmatch(r'[a-f0-9]{40}', tree.get('sha', '')):
            raise ValueError('GitHub вернул неполное дерево ресурсов')
        files = {}
        for entry in tree.get('tree', []):
            name = entry.get('path')
            if entry.get('type') == 'blob' and allowed_asset(name):
                if entry.get('mode') not in ('100644', '100755'):
                    raise ValueError('Ссылки вместо файлов ресурсов не поддерживаются')
                if not re.fullmatch(r'[a-f0-9]{40}', entry.get('sha', '')):
                    raise ValueError('Неверный хеш ресурса')
                files[name] = {'sha': entry['sha'], 'size': int(entry.get('size', 0))}
        if not files or len(files) > 4000 or any(not 0 <= f['size'] <= MAX_FILE_BYTES for f in files.values()):
            raise ValueError('Некорректный размер или список ресурсов GitHub')
        if sum(f['size'] for f in files.values()) > MAX_TOTAL_BYTES:
            raise ValueError('Ресурсы GitHub превышают 64 МБ')
        next_state = {'source': source, 'files': files, 'etag': response.headers.get('ETag', ''), 'bundle_revision': self.bundle_revision}
        if self.state.get('source') != source or 'files' not in self.state:
            # First connection establishes the remote baseline, preserving unpublished local changes.
            write_json(self.root / 'state.json', next_state)
            with self.lock:
                self.state = next_state
            return
        old_files = self.state['files']
        changed = [name for name in files if files[name] != old_files.get(name)]
        if not changed and set(files) == set(old_files):
            next_state.update({k: self.state[k] for k in ('revision', 'updated_at') if k in self.state})
            write_json(self.root / 'state.json', next_state)
            with self.lock:
                self.state = next_state
            return
        revision = tree['sha']
        destination = self.root / 'versions' / revision
        destination.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 90
        # Raw URLs use one immutable commit; downloads don't consume REST request quota.
        for name, info in files.items():
            if self.stopped.is_set() or time.monotonic() > deadline:
                raise TimeoutError('Загрузка ресурсов отложена; текущая версия сохранена')
            old = self.asset(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name not in changed:
                if old:
                    target.write_bytes(old.read_bytes())
                continue
            blob = self.session.get(f'https://raw.githubusercontent.com/{repo}/{commit_sha}/{quote(name, safe="/")}', timeout=(3, 12))
            if blob.status_code in (403, 429):
                self.next_check_at = time.time() + 3600
                raise RuntimeError('GitHub ограничил загрузку; текущая версия сохранена')
            blob.raise_for_status()
            content = blob.content
            actual = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            if len(content) != info['size'] or actual != info['sha']:
                raise ValueError(f'Файл {name} не прошёл проверку целостности')
            target.write_bytes(content)
        write_json(destination / 'complete.json', {'revision': revision})
        next_state.update(revision=revision, updated_at=time.time())
        write_json(self.root / 'state.json', next_state)
        with self.lock:
            self.state = next_state
            self.active = destination
