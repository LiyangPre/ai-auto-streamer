"""Resume offline song preparation, export aligned mixes, then import without playback."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from export_converted_songs import export_track, publish
from atomic_json import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/songs-converted'
STATE = OUTPUT / 'pipeline-state.json'


def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


@contextmanager
def exclusive_job():
    path = OUTPUT / 'pipeline.lock'
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


def coordinator_is_stale(state, grace_seconds=30):
    """A stale heartbeat alone cannot prove that expensive work has stopped."""
    updated = state.get('updated')
    if type(updated) in (int, float) and time.time() - updated <= grace_seconds:
        return False
    try:
        with exclusive_job():
            return True
    except OSError:
        # A live owner (or an unavailable lease file) must not be mistaken for
        # permission to launch another expensive conversion.
        return False


def publish_telemetry(path, value):
    """A locked progress file must never tear down conversion workers."""
    return write_json_atomic(path, value, best_effort=True)


def assert_voice(jobs, reference):
    if not reference.get('approved') or reference.get('reference_sha256') != jobs['reference_sha256']:
        raise ValueError('Approved voice metadata does not match this batch.')
    with Path(jobs['reference']).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != jobs['reference_sha256']:
            raise ValueError('Voice reference audio changed.')
    current = read(ROOT / 'data/character-profiles.json')['profiles']['astra']['voice']
    if (current['provider'] != 'mimo-tts' or current['mimo_style'] != reference['style']
            or current['mimo_model'] != reference['model']):
        raise ValueError('Saved voice changed; preparation paused to avoid producing the wrong voice.')


def launch(name, command):
    log = (OUTPUT / (name + '.log')).open('a', encoding='utf-8')
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return process, log


def stop_worker(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # The Windows venv executable launches the actual interpreter as a
        # child. Stop our owned tree, rather than orphaning the CPU worker.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def import_library(jobs, reference):
    base = 'http://127.0.0.1:8000'
    deadline = time.monotonic() + 300
    while True:
        assert_voice(jobs, reference)
        if (OUTPUT / 'pipeline.stop').exists():
            raise InterruptedError('Preparation stopped before library import.')
        request = urllib.request.Request(base + '/api/singing/import-prepared', data=b'{}', method='POST',
                    headers={'Content-Type': 'application/json', 'X-Studio-API': '1', 'Origin': base})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = read_http_error(error)
            if isinstance(detail, dict) and detail.get('code') == 'voice_changed':
                raise ValueError('Current voice changed before library import.') from None
            if (error.code == 409 and isinstance(detail, dict)
                    and detail.get('code') in {'stage_busy', 'preparation_busy'}):
                # Finite audio can outlast the generic retry budget. Waiting
                # here incurs no synthesis/conversion and keeps checking voice
                # and the explicit stop flag at the top of every attempt.
                time.sleep(5)
                continue
            if error.code == 409 and time.monotonic() < deadline:
                time.sleep(5)
                continue
            raise RuntimeError('Library import API returned HTTP ' + str(error.code)) from None
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ConnectionRefusedError):
                raise RuntimeError('Cannot reach local library import API.') from None
            command = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/import_prepared_songs.py'),
                       '--manifest', str(OUTPUT / 'prepared.json')]
            for title in jobs.get('replace_titles', []):
                command.extend(['--replace-title', title])
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            if result.returncode:
                raise RuntimeError('Offline library import failed: ' + result.stderr[-600:])
            return read(OUTPUT / 'import-result.json')


def read_http_error(error):
    try:
        return json.loads(error.read(16384)).get('detail')
    except (OSError, ValueError, AttributeError):
        return None


def record_request_status(status, exports):
    path = ROOT / 'data/song-requests.json'
    data = read(path)
    if not data:
        return
    data['conversion_status'] = status
    selected = set(data.get('current_batch_bvids', []))
    for track in data.get('tracks', []):
        bvid = track.get('source_recording', {}).get('bvid')
        if selected and bvid not in selected:
            continue
        if bvid in exports:
            track['conversion_status'] = 'ready' if status == 'complete' else 'mixed'
            track['prepared_audio'] = {key: exports[bvid][key] for key in ('mix_mp3', 'vocals_mp3', 'duration_seconds')}
        elif status in ('preparing', 'error', 'stopped'):
            track['conversion_status'] = 'queued' if status == 'preparing' else status
    publish_telemetry(path, data)


def worker_limit(requested, tracks):
    # Reserve space for Windows, the live controller and separation. Full-song
    # attention workspaces are larger than the four-second audition workspace.
    memory_limit = 1
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            memory_limit = max(1, int((memory.available / 1024 ** 3 - 4) // 4))
    return min(requested, len(tracks), memory_limit)


def shard_tracks(tracks, count):
    groups, durations = [[] for _ in range(count)], [0.] * count
    for track in sorted(tracks, key=lambda value: value['source_duration'], reverse=True):
        index = min(range(count), key=durations.__getitem__)
        groups[index].append(track['id'])
        durations[index] += track['source_duration']
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--stop', action='store_true', help='Request a safe stop; completed chunks can be resumed.')
    parser.add_argument('--steps', type=int, choices=range(4, 101), default=50)
    parser.add_argument('--workers', type=int, choices=range(1, 13), default=2,
                        help='Requested concurrency ceiling; bounded by tracks and available RAM.')
    parser.add_argument('--threads-per-worker', type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.status:
        state = read(STATE)
        if state.get('state') == 'preparing' and coordinator_is_stale(state):
            state.update(state='interrupted', stage='needs_resume',
                         message='Coordinator stopped; completed audio and checkpoints are preserved.')
            state.pop('estimated_seconds_remaining', None)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    stop = OUTPUT / 'pipeline.stop'
    if args.stop:
        stop.write_text('stop', encoding='utf-8')
        print('Stop requested.')
        return
    with exclusive_job():
        stop.unlink(missing_ok=True)
        jobs = read(OUTPUT / 'jobs.json')
        reference = read(Path(jobs['reference_metadata']))
        assert_voice(jobs, reference)
        worker_count = worker_limit(args.workers, jobs['tracks'])
        groups = shard_tracks(jobs['tracks'], worker_count)
        state = {'state': 'preparing', 'stage': 'starting', 'ready': 0, 'total': len(jobs['tracks']),
                 'current_title': '', 'message': 'Preparing offline with the current saved voice.',
                 'started': time.time(), 'updated': time.time(), 'pid': os.getpid(),
                 'workers': worker_count, 'requested_workers': args.workers,
                 'worker_ceiling': min(12, jobs.get('max_workers', 12)), 'diffusion_steps': args.steps}
        publish_telemetry(STATE, state)
        children = []
        try:
            python = str(ROOT / '.venv-svc/Scripts/python.exe')
            separator = launch('separation', [python, '-X', 'utf8', str(ROOT / 'scripts/separate_song_sources.py'),
                                             '--manifest', str(OUTPUT / 'sources.json'),
                                             '--offline', '--threads', '4', '--benchmark-seconds', '0'])
            children.append(separator)
            converters = []
            conversion_states = []
            for index, group in enumerate(groups):
                name = 'main' if worker_count == 1 else 'w' + str(index + 1)
                command = [python, '-X', 'utf8', str(ROOT / 'scripts/seed_vc_batch.py'),
                           '--steps', str(args.steps), '--threads', str(args.threads_per_worker),
                           '--worker-id', name, '--watch']
                for token in group:
                    command.extend(['--only-id', token])
                converter = launch('conversion-' + name, command)
                converters.append(converter)
                children.append(converter)
                conversion_states.append(OUTPUT / ('conversion-state.json' if name == 'main' else 'conversion-' + name + '-state.json'))
            state['worker_pids'] = [process.pid for process, _ in children]
            prepared = {key: jobs[key] for key in ('voice_candidate', 'reference', 'reference_metadata', 'reference_sha256')}
            exports = {}
            while True:
                if stop.exists():
                    raise InterruptedError('Preparation stopped; run this command again to resume.')
                assert_voice(jobs, reference)
                if read(OUTPUT / 'jobs.json').get('reference_sha256') != jobs['reference_sha256']:
                    raise ValueError('Batch reference changed; preparation stopped.')
                for track in jobs['tracks']:
                    if track['id'] not in exports:
                        result = export_track(track, jobs, required_steps=args.steps)
                        if result:
                            exports[track['id']] = result
                prepared.update(tracks=list(exports.values()), ready=len(exports), total=len(jobs['tracks']))
                # Intermediate manifests are progress snapshots. A complete
                # manifest is committed strictly below before library import.
                publish_telemetry(OUTPUT / 'prepared.json', prepared)
                record_request_status('preparing', exports)
                separation = read(OUTPUT / 'separation-state.json')
                conversions = [read(path) for path in conversion_states]
                active_conversions = [value.get('active') for value in conversions if value.get('active')]
                active = active_conversions[0] if active_conversions else separation.get('active') or {}
                titles = [value.get('title', '') for value in active_conversions]
                state.update(stage='converting' if active_conversions else 'separating',
                             ready=len(exports), current_title=active.get('title', ''), updated=time.time(),
                             separated=len(separation.get('tracks', {})),
                             converted=sum(len(value.get('tracks', {})) for value in conversions))
                if titles:
                    state['current_title'] = ' / '.join(titles)[:80]
                estimates = [value.get('estimated_seconds_remaining') for value in conversions]
                if all(isinstance(value, (int, float)) for value in estimates):
                    state['estimated_seconds_remaining'] = max(0, *estimates)
                state['message'] = f'{args.steps} steps; {worker_count} conversion workers; source melody and timing preserved.'
                publish_telemetry(STATE, state)
                if len(exports) == len(jobs['tracks']):
                    break
                for process, _ in children:
                    if process.poll() not in (None, 0):
                        raise RuntimeError('A preparation worker failed; see its local log for the error.')
                if all(converter[0].poll() == 0 for converter in converters):
                    raise RuntimeError('Conversion ended before all mixes were ready.')
                time.sleep(5)
            state.update(stage='importing', current_title='', message='Mixes ready; saving song library and backup.', updated=time.time())
            publish(OUTPUT / 'prepared.json', prepared)
            publish_telemetry(STATE, state)
            assert_voice(jobs, reference)
            result = import_library(jobs, reference)
            publish(OUTPUT / 'import-result.json', result)
            state.update(state='complete', stage='complete', ready=len(exports), updated=time.time(),
                         message='Songs, vocal mouth guides and library backup are ready. Playback was not started.')
            publish_telemetry(STATE, state)
            record_request_status('complete', exports)
        except BaseException as error:
            state.update(state='stopped' if isinstance(error, (InterruptedError, KeyboardInterrupt)) else 'error',
                         message=str(error) or 'Preparation stopped.', updated=time.time())
            publish_telemetry(STATE, state)
            record_request_status(state['state'], locals().get('exports', {}))
            raise
        finally:
            for process, log in children:
                stop_worker(process)
                log.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
