"""Import completed, aligned Astra conversions while Studio is stopped.

No provider calls or playback occur. The prepared manifest contains a voice candidate,
approved reference SHA-256 and ready mixed/vocal MP3 pairs. Each take is published
only after both audio files are copied, then the completed library is backed up.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import socket
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutagen.mp3 import MP3
from mutagen import MutagenError
from src.config import Config, PROJECT_ROOT
from src.singing import MAX_LIBRARY_BYTES, MAX_SONGS_PER_CHARACTER, SingingStudio, songs_directory
from src.studio import settings_path


MAX_AUDIO_BYTES = 20 * 1024 * 1024
ALIGNMENT_TOLERANCE = .1


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_audio(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.mp3' or not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
        raise ValueError('Prepared audio must be an MP3 of at most 20 MB.')
    try:
        info = MP3(path).info
    except MutagenError:
        raise ValueError('Prepared audio is not a readable MP3.') from None
    if info.layer != 3 or info.sketchy or not math.isfinite(info.length) or not 0 < info.length <= 600:
        raise ValueError('Prepared MP3 must contain valid audio of at most ten minutes.')
    return {'path': path, 'bytes': path.stat().st_size, 'duration': info.length, 'sha256': sha256(path)}


def validate_reference(manifest, metadata_path, profile_id='astra'):
    metadata_path = Path(metadata_path)
    reference = json.loads(metadata_path.read_text(encoding='utf-8'))
    expected = manifest.get('reference_sha256', '')
    candidate = manifest.get('voice_candidate', '')
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', candidate) or reference.get('approved') is not True
            or reference.get('candidate_id') != candidate or not re.fullmatch(r'[a-f0-9]{64}', expected)
            or reference.get('profile_id', profile_id) != profile_id
            or expected != reference.get('reference_sha256')):
        raise ValueError('Prepared tracks must use the matching approved voice reference.')
    reference_path = Path(reference.get('path') or manifest['reference'])
    if not reference_path.is_absolute():
        reference_path = PROJECT_ROOT / reference_path
    if sha256(reference_path) != expected:
        raise ValueError('Approved voice reference changed after preparation.')
    if manifest.get('reference'):
        supplied = Path(manifest['reference'])
        if not supplied.is_absolute():
            supplied = PROJECT_ROOT / supplied
        if sha256(supplied) != expected:
            raise ValueError('Manifest voice reference does not match approved audio.')
    return reference


def validate_track(track, reference_hash):
    bvid = track.get('bvid', '')
    if not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid):
        raise ValueError('A prepared track requires a valid Bilibili source ID.')
    title = str(track.get('title', '')).strip()
    if not title or len(title) > 80:
        raise ValueError('A prepared title must contain 1–80 characters.')
    source_url = track.get('source_url', f'https://www.bilibili.com/video/{bvid}/')
    url = urlsplit(source_url)
    if (url.scheme != 'https' or url.hostname not in {'www.bilibili.com', 'bilibili.com'}
            or url.username or url.password or url.port not in (None, 443) or url.query or url.fragment
            or url.path.rstrip('/') != f'/video/{bvid}'):
        raise ValueError('Source URL does not match the track BVID.')
    mix = read_audio(track['mix_mp3'])
    guide = read_audio(track['vocals_mp3'])
    if abs(mix['duration'] - guide['duration']) > ALIGNMENT_TOLERANCE:
        raise ValueError('Converted vocal guide and full mix must have matching duration (within 0.1 seconds).')
    declared = track.get('duration_seconds', mix['duration'])
    if not isinstance(declared, (int, float)) or not math.isfinite(declared) or abs(declared - mix['duration']) > ALIGNMENT_TOLERANCE:
        raise ValueError('Manifest duration does not match the prepared mix.')
    conversion = track.get('conversion', {})
    if not isinstance(conversion, dict) or not conversion.get('engine'):
        raise ValueError('Prepared tracks require conversion provenance.')
    # Public song metadata contains conversion settings, never arbitrary manifest data.
    allowed = {'engine', 'model', 'diffusion_steps', 'f0_condition', 'auto_f0_adjust',
               'length_adjust', 'semitones', 'semi_tone_shift', 'fp16', 'cfg_rate'}
    settings = {key: value for key, value in conversion.items() if key in allowed}
    json.dumps(settings, allow_nan=False)
    identity = hashlib.sha256(json.dumps([bvid, reference_hash, mix['sha256']], separators=(',', ':')).encode()).hexdigest()
    return {'bvid': bvid, 'title': title, 'source_url': source_url, 'mix': mix, 'guide': guide,
            'conversion': settings, 'import_key': identity}


@contextmanager
def import_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.prepared-import.lock'
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError:
        raise ValueError('Another import is running. A stale .prepared-import.lock may be removed only after its process stops.') from None
    try:
        with stream:
            stream.write(str(__import__('os').getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def ensure_studio_stopped(host, port):
    host = '127.0.0.1' if host in {'0.0.0.0', 'localhost'} else host
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except ConnectionRefusedError:
        return
    except OSError as error:
        raise ValueError(f'Could not verify Studio is stopped ({type(error).__name__}); import aborted.') from None
    raise ValueError(f'Stop Studio on {host}:{port} before importing the prepared library.')


def publish_track(studio, track, profile_id, reference):
    token = uuid.uuid4().hex
    root = studio.root
    audio = root / (token + '.mp3')
    guide = root / (token + '-guide.mp3')
    metadata = root / (token + '.json')
    pending = [audio.with_suffix('.mp3.part'), guide.with_suffix('.mp3.part'), metadata.with_suffix('.json.tmp')]
    item = {'id': token, 'title': track['title'], 'profile_id': profile_id,
            'duration': round(track['mix']['duration'], 3), 'bytes': track['mix']['bytes'],
            'guide_bytes': track['guide']['bytes'], 'source': 'upload',
            'voice': reference.get('name') or reference.get('candidate_id', 'Approved voice'),
            'model': 'offline-voice-conversion', 'created': time.time(), 'generation_ms': 0, 'lyrics': '',
            'url': f'/api/singing/audio/{token}', 'guide_url': f'/api/singing/audio/{token}/guide',
            'conversion_provenance': {'import_key': track['import_key'], 'bvid': track['bvid'],
                'source_url': track['source_url'], 'voice_candidate': reference['candidate_id'],
                'reference_sha256': reference['reference_sha256'], 'mix_sha256': track['mix']['sha256'],
                'vocals_sha256': track['guide']['sha256'], 'settings': track['conversion']}}
    committed = False
    try:
        shutil.copyfile(track['mix']['path'], pending[0])
        shutil.copyfile(track['guide']['path'], pending[1])
        if sha256(pending[0]) != track['mix']['sha256'] or sha256(pending[1]) != track['guide']['sha256']:
            raise ValueError('Prepared audio changed while being imported.')
        pending[2].write_text(json.dumps(item, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        pending[0].replace(audio)
        pending[1].replace(guide)
        pending[2].replace(metadata)
        committed = True
    finally:
        for path in pending:
            path.unlink(missing_ok=True)
        if not committed:
            audio.unlink(missing_ok=True)
            guide.unlink(missing_ok=True)
    return item


def import_prepared(manifest_path, library_root, reference_metadata=None, profile_id='astra', *, stopped_check=None,
                    expected_total=None, expected_bvids=None, expected_reference_hash=None, expected_steps=None):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', profile_id):
        raise ValueError('Invalid profile ID.')
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if expected_total is not None:
        rows = manifest.get('tracks')
        if (manifest.get('ready') != expected_total or manifest.get('total') != expected_total
                or not isinstance(rows, list) or len(rows) != expected_total
                or any(not isinstance(row, dict) or row.get('ready') is not True for row in rows)):
            raise ValueError('The complete prepared batch is not ready yet.')
    if expected_bvids is not None:
        actual = [track.get('bvid') for track in manifest.get('tracks', [])]
        if len(actual) != len(set(actual)) or set(actual) != set(expected_bvids):
            raise ValueError('Prepared tracks no longer match the active batch.')
    if expected_reference_hash is not None and manifest.get('reference_sha256') != expected_reference_hash:
        raise ValueError('Prepared reference no longer matches the active batch.')
    if expected_steps is not None and any(track.get('conversion', {}).get('diffusion_steps') != expected_steps for track in manifest.get('tracks', [])):
        raise ValueError('Prepared tracks no longer match the requested conversion quality.')
    reference_metadata = Path(reference_metadata or manifest.get('reference_metadata') or PROJECT_ROOT / 'data/voice-references/astra-voicedesign.json')
    if not reference_metadata.is_absolute():
        reference_metadata = PROJECT_ROOT / reference_metadata
    reference = validate_reference(manifest, reference_metadata, profile_id)
    tracks = [validate_track(track, manifest['reference_sha256']) for track in manifest['tracks'] if track.get('ready') is True]
    if len({item['import_key'] for item in tracks}) != len(tracks):
        raise ValueError('The prepared manifest contains duplicate track versions.')
    root = Path(library_root).resolve()
    with import_lock(root):
        if stopped_check:
            stopped_check()
        studio = SingingStudio(root, SimpleNamespace())
        catalog = studio.catalog()
        existing = {song.get('conversion_provenance', {}).get('import_key'): song for song in catalog if song['profile_id'] == profile_id}
        pending, skipped = [], []
        for track in tracks:
            song = existing.get(track['import_key'])
            if song:
                if (sha256(root / (song['id'] + '.mp3')) != track['mix']['sha256']
                        or not (root / (song['id'] + '-guide.mp3')).is_file()
                        or sha256(root / (song['id'] + '-guide.mp3')) != track['guide']['sha256']):
                    raise ValueError('An existing imported take is missing or damaged; restore it before retrying.')
                skipped.append({'bvid': track['bvid'], 'song_id': song['id']})
            else:
                pending.append(track)
        extra_bytes = sum(item['mix']['bytes'] + item['guide']['bytes'] for item in pending)
        if (sum(song['profile_id'] == profile_id for song in catalog) + len(pending) > MAX_SONGS_PER_CHARACTER
                or len(catalog) + len(pending) > 256):
            raise ValueError('Prepared batch would exceed the song library track limit.')
        if sum(song.get('bytes', 0) + song.get('guide_bytes', 0) for song in catalog) + extra_bytes > MAX_LIBRARY_BYTES:
            raise ValueError('Prepared batch would exceed the 1 GB song library limit.')
        backup_root = root.with_name(root.name + '-backups') / profile_id
        if pending and len(list(backup_root.glob('*.zip'))) >= 8:
            raise ValueError('Move an old song backup first; eight archives already exist.')
        if stopped_check:
            stopped_check()
        imported = [publish_track(studio, track, profile_id, reference) for track in pending]
        backup = studio.backup(profile_id) if imported else None
        if backup:
            backup['path'] = str(backup_root / (backup['id'] + '.zip'))
        result = {'profile_id': profile_id, 'voice_candidate': reference['candidate_id'], 'reference_sha256': manifest['reference_sha256'],
                  'imported': [{'bvid': song['conversion_provenance']['bvid'], 'song_id': song['id'], 'title': song['title']} for song in imported],
                  'skipped': skipped, 'backup': backup, 'playback_started': False}
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=PROJECT_ROOT / 'data/songs-converted/prepared.json')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--reference-metadata', type=Path,
                        help='Defaults to the manifest reference_metadata, then the legacy Astra reference metadata.')
    args = parser.parse_args()
    config = Config.load(args.config)
    try:
        result = import_prepared(args.manifest, songs_directory(settings_path(args.config)), args.reference_metadata,
                                 stopped_check=lambda: ensure_studio_stopped(config.server.host, config.server.port))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Import failed: {error}\n')
    destination = args.manifest.with_name('import-result.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
