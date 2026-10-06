"""Build the signed cumulative source update; keep the signing key outside Git."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import update_client
from version import __version__


def build(source, key_file, revision, output):
    files = {}
    for path in source.glob('*.py'):
        if path.name not in {'update_client.py', 'xlambot_launcher.py', 'setup.py'} and not path.name.startswith(('test_', 'tools_')):
            files[path.name] = path
    for folder, suffixes in {'webui': {'.py'}, 'api': {'.py'}, 'static': {'.js', '.css'}, 'templates': {'.html'}}.items():
        for path in (source / folder).rglob('*'):
            if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts:
                files[path.relative_to(source).as_posix()] = path
    for name in files:
        update_client.safe_name(name)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / 'scripts.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            archive.writestr(name, path.read_bytes())
    data = archive_path.read_bytes()
    manifest = {'repository': update_client.REPOSITORY, 'bootstrap': update_client.BOOTSTRAP,
                'revision': revision, 'version': __version__, 'python': '3.12',
                'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'files': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())}}
    key = Ed25519PrivateKey.from_private_bytes(key_file.read_bytes())
    envelope = {'manifest': manifest, 'signature': key.sign(update_client.canonical(manifest)).hex()}
    update_client.verify(envelope)
    (output / 'manifest.json').write_text(json.dumps(envelope, ensure_ascii=False, indent=2)+'\n', 'utf8')
    print(f'{len(files)} signed files; revision {revision}; {len(data)} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--revision', type=int, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if args.revision < 1:
        parser.error('revision must be positive')
    build(Path(__file__).resolve().parent, args.key, args.revision, args.output)
