"""Queue one downloaded song after the current batch finishes and is imported.

This waiter loads metadata only. It never stops an active coordinator or worker,
calls a provider, starts playback, or changes Astra's selected voice.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_astra_songs as coordinator

try:
    from .atomic_json import write_json_atomic
except ImportError:  # Direct script execution.
    from atomic_json import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/songs-converted'


class QueueBlocked(ValueError):
    pass


def read(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        return json.load(stream)


def publish(path, value, *, best_effort=False):
    return write_json_atomic(path, value, best_effort=best_effort)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def ids(tracks):
    if not isinstance(tracks, list) or not tracks:
        raise QueueBlocked('The active batch has no tracks.')
    result = [track.get('bvid') for track in tracks]
    if (any(not isinstance(token, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', token) for token in result)
            or len(set(result)) != len(result)):
        raise QueueBlocked('Batch identifiers are invalid or duplicated.')
    return result


def confined(path, root):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise QueueBlocked('A queued source/output path is outside its intended directory.')
    return resolved


def validate_pending(pending):
    if pending.get('version') != 1 or pending.get('diffusion_steps') != 50:
        raise QueueBlocked('The follow-on batch must retain 50-step conversion quality.')
    expected = pending.get('expected_batch_ids')
    if not isinstance(expected, list) or ids([{'bvid': value} for value in expected]) != expected:
        raise QueueBlocked('The original batch identifiers are missing.')
    track = pending.get('track', {})
    token = track.get('bvid')
    if ids([track]) != [token] or track.get('id') != token or token in expected:
        raise QueueBlocked('The queued song must be new and have a matching BVID.')
    if not isinstance(track.get('title'), str) or not 1 <= len(track['title']) <= 80:
        raise QueueBlocked('The queued song title is invalid.')
    reference_hash = pending.get('reference_sha256')
    if not isinstance(reference_hash, str) or not re.fullmatch('[a-f0-9]{64}', reference_hash):
        raise QueueBlocked('The approved voice reference hash is missing.')
    if (track.get('reference_sha256') != reference_hash or not pending.get('voice_candidate')
            or pending.get('source', {}).get('bvid') != token
            or pending.get('request_track', {}).get('source_recording', {}).get('bvid') != token):
        raise QueueBlocked('The queued source, request and voice metadata do not agree.')
    source = confined(track['source'], ROOT / 'data/song-sources')
    if not source.is_file() or not source.stat().st_size:
        raise QueueBlocked('The queued source has not finished downloading.')
    with source.open('rb') as stream:
        source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if source_hash != track.get('source_sha256'):
        raise QueueBlocked('The queued source changed after download.')
    duration = track.get('source_duration')
    if type(duration) not in (int, float) or not 0 < duration <= 600:
        raise QueueBlocked('The queued source duration is invalid.')
    if (Path(pending['source'].get('original_audio', '')).resolve() != source
            or pending['source'].get('ok') is not True):
        raise QueueBlocked('The separation manifest does not describe this completed source.')
    directory = confined(track['output_dir'], OUTPUT)
    if directory != (OUTPUT / token).resolve():
        raise QueueBlocked('The queued output directory must be named for its BVID.')
    for key, filename in [('vocals', 'vocals.wav'), ('accompaniment', 'accompaniment.wav')]:
        if Path(track[key]).resolve() != directory / filename:
            raise QueueBlocked('The queued stem paths do not match the song directory.')
    return token


@contextmanager
def waiter_lock(path):
    """An OS lease releases automatically if the background waiter is interrupted."""
    path = Path(path)
    with path.open('a+b') as stream:
        if not path.stat().st_size:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def batch_completed(batch_ids, reference_hash):
    """Completion includes validated export AND successful library import."""
    try:
        state = read(OUTPUT / 'pipeline-state.json')
        prepared = read(OUTPUT / 'prepared.json')
        imported = read(OUTPUT / 'import-result.json')
        actual = ids(prepared.get('tracks'))
        saved = [row.get('bvid') for row in imported.get('imported', []) + imported.get('skipped', [])]
        return (state.get('state') == 'complete' and state.get('stage') == 'complete'
                and state.get('total') == len(batch_ids) and state.get('ready') == len(batch_ids)
                and state.get('diffusion_steps') == 50
                and prepared.get('total') == len(batch_ids) and prepared.get('ready') == len(batch_ids)
                and prepared.get('reference_sha256') == reference_hash
                and imported.get('reference_sha256') == reference_hash
                and imported.get('playback_started') is False
                and set(actual) == set(batch_ids) and set(saved) == set(batch_ids)
                and all(track.get('ready') is True and track.get('conversion', {}).get('diffusion_steps') == 50
                        for track in prepared['tracks']))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def inspect_batch(pending):
    jobs = read(OUTPUT / 'jobs.json')
    expected = pending['expected_batch_ids']
    token = pending['track']['bvid']
    current_ids = ids(jobs.get('tracks'))
    if current_ids not in (expected, expected + [token]):
        raise QueueBlocked('The active batch changed. Review the pending request before continuing.')
    if (jobs.get('reference_sha256') != pending['reference_sha256']
            or jobs.get('voice_candidate') != pending['voice_candidate']
            or jobs.get('diffusion_steps') != pending['diffusion_steps']):
        raise QueueBlocked('The active voice or conversion quality changed.')
    for key in ('reference', 'reference_metadata'):
        if Path(pending['track'][key]).resolve() != Path(jobs[key]).resolve():
            raise QueueBlocked('The queued voice paths do not match the approved batch.')
    coordinator.assert_voice(jobs, read(Path(jobs['reference_metadata'])))
    base = {**jobs, 'tracks': jobs['tracks'][:len(expected)]}
    base_hash = fingerprint(base)
    if pending.get('base_jobs_sha256') not in (None, base_hash):
        raise QueueBlocked('The original batch metadata changed after this song was queued.')
    pending['base_jobs_sha256'] = base_hash
    if current_ids == expected + [token] and jobs['tracks'][-1] != pending['track']:
        raise QueueBlocked('Another queued recording already uses this BVID.')
    return jobs, current_ids


def activate(pending):
    """Called under coordinator.exclusive_job(), only after the old run ended."""
    validate_pending(pending)  # Source may have changed during the long wait.
    jobs, current_ids = inspect_batch(pending)
    token = pending['track']['bvid']
    if current_ids == pending['expected_batch_ids'] + [token]:
        return False
    if (OUTPUT / 'pipeline.stop').exists():
        raise QueueBlocked('A preparation stop was requested; explicitly resume before activating the queued song.')
    if not batch_completed(current_ids, pending['reference_sha256']):
        raise QueueBlocked('The original batch has not completed its library import.')
    sources = read(OUTPUT / 'sources.json')
    source_ids = ids(sources)
    # A crash after sources.json but before jobs.json is safe to resume.
    if source_ids not in (current_ids, current_ids + [token]):
        raise QueueBlocked('The separation sources changed unexpectedly.')
    if token in source_ids and sources[-1] != pending['source']:
        raise QueueBlocked('A different source is already queued under this BVID.')
    requests_path = ROOT / 'data/song-requests.json'
    requests = read(requests_path)
    if requests.get('current_batch_bvids') not in (current_ids, current_ids + [token]):
        raise QueueBlocked('The selected request batch changed unexpectedly.')
    existing = [track for track in requests.get('tracks', [])
                if track.get('source_recording', {}).get('bvid') == token]
    if existing and existing != [pending['request_track']]:
        raise QueueBlocked('A different song request already uses this recording.')
    if token not in source_ids:
        publish(OUTPUT / 'sources.json', sources + [pending['source']])
    if not existing:
        requests['tracks'].append(pending['request_track'])
    requests.update(current_batch_bvids=current_ids + [token], current_batch_total=len(current_ids) + 1,
                    conversion_status='queued', diffusion_steps=50)
    publish(requests_path, requests)
    # jobs.json is the transaction's commit marker and is replaced last. The
    # exclusive coordinator lease prevents a new worker reading partial inputs.
    publish(OUTPUT / 'jobs.json', {**jobs, 'tracks': jobs['tracks'] + [pending['track']]})
    return True


def launch_follow_on(pending):
    command = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/prepare_astra_songs.py'),
               '--steps', '50', '--workers', '1', '--threads-per-worker', '6']
    log_path = OUTPUT / ('follow-on-' + pending['track']['bvid'] + '.log')
    with log_path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return process


def run(pending_path, watch=False, interval=10):
    pending_path = Path(pending_path).resolve()
    confined(pending_path, OUTPUT)
    with waiter_lock(pending_path.with_suffix('.waiter.lock')):
        process = None
        validated = None
        while True:
            pending = read(pending_path)
            if pending.get('state') in {'cancelled', 'complete'}:
                return pending
            pending.update(waiter_pid=os.getpid(), updated=time.time())
            try:
                # Hash the downloaded source once per immutable request, not on
                # every heartbeat while a multi-hour conversion is running.
                signature = fingerprint({k: pending[k] for k in ('track', 'source', 'request_track',
                                                                 'expected_batch_ids', 'reference_sha256')})
                if validated != signature:
                    validate_pending(pending)
                    validated = signature
                jobs, current_ids = inspect_batch(pending)
                expected = pending['expected_batch_ids']
                token = pending['track']['bvid']
                if current_ids == expected and not batch_completed(expected, pending['reference_sha256']):
                    state = coordinator.read(OUTPUT / 'pipeline-state.json')
                    pending.update(state='queued', stage='waiting_for_current_batch',
                                   message='Waiting for the current songs and their library import; no active worker is interrupted.')
                    if (state.get('state') in {'error', 'stopped'}
                            or coordinator.coordinator_is_stale(state)):
                        pending.update(state='blocked', message='Current preparation stopped; resume that batch before the queued song can start.')
                elif current_ids == expected:
                    try:
                        with coordinator.exclusive_job():
                            # Recheck after acquiring the actual coordinator
                            # lease; a stale "complete" flag alone is not enough.
                            activate(pending)
                        pending.update(state='activated', stage='starting_follow_on', activated_at=time.time(),
                                       message='Original songs imported. Adding the queued song with completed audio reused.')
                        publish(pending_path, pending)
                        process = launch_follow_on(pending)
                        pending['coordinator_pid'] = process.pid
                    except (BlockingIOError, PermissionError):
                        pending.update(state='queued', stage='waiting_for_cleanup',
                                       message='Original import completed; waiting for its worker cleanup and coordinator lease.')
                elif batch_completed(current_ids, pending['reference_sha256']):
                    pending.update(state='complete', stage='complete', completed_at=time.time(),
                                   message='Queued song converted and imported. Playback was not started.')
                else:
                    pending.update(state='activated', stage='follow_on_preparing',
                                   message='Follow-on preparation running; completed original songs are reused.')
                    if process is not None and process.poll() is not None:
                        pending.update(state='blocked', message='Follow-on coordinator stopped. Completed audio is preserved; inspect its log before resuming.')
                    elif process is None:
                        # Recovery after this waiter was interrupted. Probe the
                        # lease without holding it while launching the child.
                        try:
                            if coordinator.coordinator_is_stale(coordinator.read(OUTPUT / 'pipeline-state.json')):
                                raise QueueBlocked('Follow-on preparation stopped; explicitly resume it before continuing.')
                            with coordinator.exclusive_job():
                                state = coordinator.read(OUTPUT / 'pipeline-state.json')
                                if (state.get('state') in {'error', 'stopped'}
                                        or (OUTPUT / 'pipeline.stop').exists()):
                                    raise QueueBlocked('Follow-on preparation stopped; explicitly resume it before continuing.')
                            process = launch_follow_on(pending)
                            pending['coordinator_pid'] = process.pid
                        except (BlockingIOError, PermissionError):
                            pass
                publish(pending_path, pending, best_effort=True)
            except (OSError, ValueError, KeyError, TypeError) as error:
                pending.update(state='blocked', stage='needs_review', message=str(error)[:300])
                publish(pending_path, pending, best_effort=True)
            if not watch or pending['state'] == 'complete':
                return pending
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(read(args.pending), ensure_ascii=False, indent=2))
        return
    result = run(args.pending, watch=args.watch)
    print(json.dumps({key: result.get(key) for key in ('state', 'stage', 'message', 'waiter_pid', 'coordinator_pid')},
                     ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
