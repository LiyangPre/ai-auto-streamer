"""Recover only a completed batch's busy-stage import; never prepare or play audio.

This is deliberately a companion to an already running coordinator, not a second
conversion runner. It is bound to an immutable jobs snapshot and only takes over
after that coordinator exits with the old generic HTTP409 import timeout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_astra_songs as coordinator
from atomic_json import write_json_atomic


class FinalizationBlocked(ValueError):
    pass


def fingerprint(jobs):
    return hashlib.sha256(json.dumps(jobs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_ready(expected, prepared):
    ids = [track['id'] for track in expected['tracks']]
    rows = prepared.get('tracks', [])
    if (prepared.get('total') != len(ids) or prepared.get('ready') != len(ids)
            or len(rows) != len(ids) or {row.get('bvid') for row in rows} != set(ids)
            or prepared.get('reference_sha256') != expected['reference_sha256']
            or any(row.get('ready') is not True or row.get('conversion', {}).get('diffusion_steps') !=
                   expected['diffusion_steps'] for row in rows)):
        raise FinalizationBlocked('Completed exports no longer match the selected batch.')


def step(expected):
    output = coordinator.OUTPUT
    if fingerprint(coordinator.read(output / 'jobs.json')) != fingerprint(expected):
        raise FinalizationBlocked('The selected batch changed; finalization stopped.')
    if (output / 'pipeline.stop').exists():
        raise FinalizationBlocked('Preparation was explicitly stopped.')
    coordinator.assert_voice(expected, coordinator.read(Path(expected['reference_metadata'])))
    state = coordinator.read(coordinator.STATE)
    if state.get('state') == 'complete':
        validate_ready(expected, coordinator.read(output / 'prepared.json'))
        result = coordinator.read(output / 'import-result.json')
        actual = {row.get('bvid') for row in result.get('imported', []) + result.get('skipped', [])}
        if (actual != {track['id'] for track in expected['tracks']}
                or result.get('reference_sha256') != expected['reference_sha256']
                or result.get('playback_started') is not False):
            raise FinalizationBlocked('Completed import receipt does not match this batch.')
        return {'state': 'complete', 'recovered': False, 'playback_started': False}
    if state.get('state') in {'queued', 'preparing'}:
        return {'state': 'waiting_for_coordinator', 'stage': state.get('stage', 'waiting')}
    if not (state.get('state') == 'error' and state.get('stage') == 'importing'
            and state.get('message') == 'Library import API returned HTTP 409'):
        raise FinalizationBlocked('Coordinator needs review; only a completed busy import is recoverable.')
    validate_ready(expected, coordinator.read(output / 'prepared.json'))
    acquired = False
    try:
        with coordinator.exclusive_job():
            acquired = True
            # The old coordinator must have released its lease and children.
            if fingerprint(coordinator.read(output / 'jobs.json')) != fingerprint(expected):
                raise FinalizationBlocked('The batch changed before final import.')
            result = coordinator.import_library(expected, coordinator.read(Path(expected['reference_metadata'])))
            if result.get('playback_started') is not False:
                raise FinalizationBlocked('Import did not confirm silent publication.')
            coordinator.publish(output / 'import-result.json', result)
            state.update(state='complete', stage='complete', ready=len(expected['tracks']),
                         total=len(expected['tracks']), updated=time.time(),
                         message='Completed songs imported after waiting for the stage. Playback was not started.')
            coordinator.publish(coordinator.STATE, state)
            exports = {row['bvid']: row for row in coordinator.read(output / 'prepared.json')['tracks']}
            coordinator.record_request_status('complete', exports)
            return {'state': 'complete', 'recovered': True, 'playback_started': False}
    except (BlockingIOError, PermissionError):
        if acquired:
            raise
        return {'state': 'waiting_for_coordinator_cleanup'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=Path, required=True, help='Immutable snapshot of the selected batch jobs.')
    parser.add_argument('--poll-seconds', type=float, default=10)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 60:
        parser.error('poll-seconds must be 1..60')
    path = args.jobs.resolve()
    if not path.is_relative_to((coordinator.OUTPUT / 'batches').resolve()):
        parser.error('The snapshot must be inside data/songs-converted/batches.')
    expected = coordinator.read(path)
    if not expected.get('tracks') or expected.get('diffusion_steps') != 50:
        parser.error('A validated 50-step batch snapshot is required.')
    status_path = path.with_name('finalizer-state.json')
    previous = None
    while True:
        try:
            status = step(expected)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            status = {'state': 'blocked', 'error_type': type(error).__name__,
                      'message': 'Final import needs review; all prepared audio is preserved.'}
        status.update(updated=time.time(), pid=__import__('os').getpid(), jobs_sha256=fingerprint(expected))
        write_json_atomic(status_path, status)
        if status['state'] != previous:
            print(json.dumps(status), flush=True)
            previous = status['state']
        if status['state'] in {'complete', 'blocked'}:
            return 0 if status['state'] == 'complete' else 1
        time.sleep(args.poll_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
