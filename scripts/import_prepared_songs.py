"""Import completed, aligned Astra conversions while Studio is stopped.

No provider calls or playback occur. The prepared manifest contains a voice candidate,
approved reference SHA-256 and ready mixed/vocal MP3 pairs. Each take is published
only after both audio files are copied, then the completed library is backed up.
Optional named replacement first verifies a complete pre-replacement backup and
retains retired files outside the active catalog; it needs two free backup slots.
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
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutagen.mp3 import MP3
from mutagen import MutagenError
from src.config import Config, PROJECT_ROOT
from src.singing import MAX_LIBRARY_BYTES, MAX_SONGS_PER_CHARACTER, SingingStudio, songs_directory
from src.song_requests import normalized_title
from src.studio import settings_path
from scripts.atomic_json import write_json_atomic


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


def replacement_titles(values, tracks):
    """Replacement is explicit, profile-scoped and unambiguous in this batch."""
    if values is None:
        return set()
    if not isinstance(values, (list, tuple)) or len(values) > MAX_SONGS_PER_CHARACTER:
        raise ValueError('Replacement titles must be a list of at most 64 titles.')
    selected = set()
    for value in values:
        if not isinstance(value, str) or not 1 <= len(value) <= 80 or not normalized_title(value):
            raise ValueError('Replacement titles must contain 1–80 characters.')
        selected.add(normalized_title(value))
    incoming = {}
    for track in tracks:
        key = normalized_title(track['title'])
        if key in selected:
            if key in incoming:
                raise ValueError('A replacement title must identify exactly one prepared take.')
            incoming[key] = track
    if selected != set(incoming):
        raise ValueError('Every replacement title must occur in the validated prepared batch.')
    return selected


def song_files(root, song):
    token = song.get('id', '')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('Cannot replace a song with an invalid library identity.')
    suffixes = ['.json', '.mp3']
    if song.get('guide_url'):
        suffixes.append('-guide.mp3')
    return [root / (token + suffix) for suffix in suffixes]


def library_backup(studio, profile_id, *, verify_catalog=None):
    backup = studio.backup(profile_id)
    root = studio.root.with_name(studio.root.name + '-backups') / profile_id
    path = root / (backup['id'] + '.zip')
    backup['path'] = str(path)
    if verify_catalog is not None:
        # Do not retire an old take until its complete pre-replacement backup
        # can be read and contains the exact current bytes of every profile song.
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Pre-replacement backup did not pass its integrity check.')
                manifest = json.loads(archive.read('manifest.json'))
                for song in verify_catalog:
                    for source in song_files(studio.root, song):
                        name = 'songs/' + source.name
                        expected = sha256(source)
                        if (manifest.get('sha256', {}).get(name) != expected
                                or hashlib.sha256(archive.read(name)).hexdigest() != expected):
                            raise ValueError('Pre-replacement backup differs from the current library.')
        except (zipfile.BadZipFile, KeyError, TypeError):
            raise ValueError('Pre-replacement backup cannot be verified.') from None
    return backup


def publish_replacement(studio, tracks, profile_id, reference, retired, *, stopped_check=None):
    """Keep old takes recoverable and roll back this batch on publication errors.

    There is no multi-file filesystem transaction. The import lease and caller's
    stopped/settings guard exclude supported concurrent writers. On success the
    old files remain in a unique quarantine outside the active song catalog as
    well as in a verified whole-profile backup. On interruption an additional
    archive receipt records the exact owned files for recovery.
    """
    before = library_backup(studio, profile_id,
                            verify_catalog=[song for song in studio.catalog() if song['profile_id'] == profile_id])
    archive = studio.root.with_name(studio.root.name + '-replaced') / profile_id / uuid.uuid4().hex
    archive.mkdir(parents=True, exist_ok=False)
    sources = [(source, archive / source.name) for song in retired for source in song_files(studio.root, song)]
    receipt = {'version': 1, 'profile_id': profile_id, 'state': 'prepared', 'created': time.time(),
               'pre_replacement_backup': before,
               'old_songs': retired, 'new_songs': [],
               'files': [{'source': str(source), 'archive': str(target), 'sha256': sha256(source)}
                         for source, target in sources]}
    receipt_path = archive / 'replacement.json'
    write_json_atomic(receipt_path, receipt)
    imported, moved = [], []
    try:
        if stopped_check:
            stopped_check()
        for track in tracks:
            item = publish_track(studio, track, profile_id, reference)
            imported.append(item)
            receipt['new_songs'] = imported
            write_json_atomic(receipt_path, receipt)
        if stopped_check:
            stopped_check()
        # Every new take is already validated and completely published before
        # any old metadata is hidden. A fresh version deliberately gets no LRC
        # from the old recording; exact import-key reuses never reach this path.
        for source, target in sources:
            source.replace(target)
            moved.append((source, target))
        backup = library_backup(studio, profile_id)
        receipt.update(state='complete', completed=time.time(), post_replacement_backup=backup)
        write_json_atomic(receipt_path, receipt)
    except BaseException:
        rollback_errors = []
        for source, target in reversed(moved):
            try:
                target.replace(source)
            except OSError as error:
                rollback_errors.append(type(error).__name__)
        for song in imported:
            for path in song_files(studio.root, song):
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    rollback_errors.append(type(error).__name__)
        receipt.update(state='rollback-incomplete' if rollback_errors else 'rolled-back',
                       rollback_errors=rollback_errors, updated=time.time())
        write_json_atomic(receipt_path, receipt, best_effort=True)
        if rollback_errors:
            raise RuntimeError('Replacement rollback needs recovery from its pre-replacement backup and receipt.') from None
        raise
    return imported, backup, before, str(archive)


def import_prepared(manifest_path, library_root, reference_metadata=None, profile_id='astra', *, stopped_check=None,
                    expected_total=None, expected_bvids=None, expected_reference_hash=None, expected_steps=None,
                    replace_titles=None):
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
    selected_titles = replacement_titles(replace_titles, tracks)
    root = Path(library_root).resolve()
    with import_lock(root):
        if stopped_check:
            stopped_check()
        studio = SingingStudio(root, SimpleNamespace())
        catalog = studio.catalog()
        existing = {song.get('conversion_provenance', {}).get('import_key'): song for song in catalog if song['profile_id'] == profile_id}
        incoming_by_title = {normalized_title(track['title']): track for track in tracks
                             if normalized_title(track['title']) in selected_titles}
        retired = [song for song in catalog if song['profile_id'] == profile_id
                   and normalized_title(song['title']) in selected_titles
                   and song.get('conversion_provenance', {}).get('import_key') !=
                   incoming_by_title[normalized_title(song['title'])]['import_key']]
        for song in retired:
            for path in song_files(root, song):
                if not path.is_file():
                    raise ValueError('An old take is incomplete; restore it before replacing the recording.')
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
        if (sum(song['profile_id'] == profile_id for song in catalog) + len(pending) - len(retired) > MAX_SONGS_PER_CHARACTER
                or len(catalog) + len(pending) - len(retired) > 256):
            raise ValueError('Prepared batch would exceed the song library track limit.')
        old_bytes = sum(song.get('bytes', 0) + song.get('guide_bytes', 0) for song in retired)
        if sum(song.get('bytes', 0) + song.get('guide_bytes', 0) for song in catalog) + extra_bytes - old_bytes > MAX_LIBRARY_BYTES:
            raise ValueError('Prepared batch would exceed the 1 GB song library limit.')
        backup_root = root.with_name(root.name + '-backups') / profile_id
        backup_count = len(list(backup_root.glob('*.zip')))
        backups_needed = 2 if retired else int(bool(pending))
        if backup_count + backups_needed > 8:
            raise ValueError('Move old song backups first; replacement needs two free backup slots.' if retired
                             else 'Move an old song backup first; eight archives already exist.')
        if stopped_check:
            stopped_check()
        pre_replacement_backup = replacement_archive = None
        if retired:
            imported, backup, pre_replacement_backup, replacement_archive = publish_replacement(
                studio, pending, profile_id, reference, retired, stopped_check=stopped_check)
        else:
            imported = [publish_track(studio, track, profile_id, reference) for track in pending]
            backup = library_backup(studio, profile_id) if imported else None
        result = {'profile_id': profile_id, 'voice_candidate': reference['candidate_id'], 'reference_sha256': manifest['reference_sha256'],
                  'imported': [{'bvid': song['conversion_provenance']['bvid'], 'song_id': song['id'], 'title': song['title']} for song in imported],
                  'skipped': skipped, 'backup': backup, 'playback_started': False}
        if selected_titles:
            result.update(replaced=[{'song_id': song['id'], 'title': song['title'],
                                     'bvid': song.get('conversion_provenance', {}).get('bvid')}
                                    for song in retired],
                          pre_replacement_backup=pre_replacement_backup, replacement_archive=replacement_archive)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=PROJECT_ROOT / 'data/songs-converted/prepared.json')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--reference-metadata', type=Path,
                        help='Defaults to the manifest reference_metadata, then the legacy Astra reference metadata.')
    parser.add_argument('--replace-title', action='append', default=[],
                        help='Replace older takes of this exact normalized title after verified backup; repeat per title.')
    args = parser.parse_args()
    config = Config.load(args.config)
    try:
        result = import_prepared(args.manifest, songs_directory(settings_path(args.config)), args.reference_metadata,
                                 stopped_check=lambda: ensure_studio_stopped(config.server.host, config.server.port),
                                 replace_titles=args.replace_title)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Import failed: {error}\n')
    destination = args.manifest.with_name('import-result.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
