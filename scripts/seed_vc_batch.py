"""Offline, resumable Seed-VC v1 singing-voice conversion of separated vocals.

Run only with .venv-svc/Scripts/python.exe. Public model assets must already be
cached in .tools/seed-vc. The live application environment is never imported.
This script publishes converted vocals only; it never mixes or publishes songs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / '.tools/seed-vc'
OUTPUT_ROOT = ROOT / 'data/songs-converted'
RATE = 44100
COMMIT = '51383efd921027683c89e5348211d93ff12ac2a8'


def worker_paths(worker_id: str, output_root: Path | None = None) -> tuple[Path, Path]:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', worker_id):
        raise ValueError('Worker ID must be 1–32 letters, digits, underscores or hyphens.')
    root = OUTPUT_ROOT if output_root is None else Path(output_root)
    if worker_id == 'main':
        return root / 'conversion-state.json', root / '.seed-vc.lock'
    return root / f'conversion-{worker_id}-state.json', root / f'.seed-vc-{worker_id}.lock'


def excerpt_partial_path(path: Path, worker_id: str) -> Path:
    worker_paths(worker_id)  # Reject traversal before using an identifier in a filename.
    return path.with_name(f'{path.name}.{worker_id}.{os.getpid()}.partial')


@contextmanager
def song_lease(directory: Path):
    """A ready song belongs to one converter; overlap waits, then checks the cache."""
    from filelock import FileLock, Timeout
    lock = FileLock(str(directory / '.seed-vc-track.lock'), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        lock.release()


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def publish(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(partial, path)


def log(event: str, **fields) -> None:
    print(json.dumps({'event': event, 'time': stamp(), **fields}, ensure_ascii=False), flush=True)


def project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('All source/reference/output paths must stay inside this workspace')
    return path


def load_jobs(path: Path) -> dict:
    jobs = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(jobs.get('tracks'), list) or not jobs['tracks']:
        raise ValueError('Jobs file needs a nonempty tracks array')
    reference = project_path(jobs['reference'])
    expected = jobs['reference_sha256']
    approval = json.loads(reference.with_suffix('.json').read_text(encoding='utf-8'))
    if approval.get('approved') is not True or approval.get('reference_sha256') != expected:
        raise ValueError('Reference metadata is not approved or does not match the requested hash')
    if sha256(reference) != expected:
        raise ValueError('Reference WAV changed; no conversion is authorized for this hash')
    for track in jobs['tracks']:
        if track.get('reference_sha256') != expected or project_path(track['reference']) != reference:
            raise ValueError('Track and manifest reference disagree')
        directory = project_path(track['output_dir'])
        if not directory.is_relative_to(OUTPUT_ROOT.resolve()) or directory == OUTPUT_ROOT.resolve():
            raise ValueError('Each output_dir must be a song folder inside data/songs-converted')
        if project_path(track['vocals']).parent != directory:
            raise ValueError('Separated vocals must live in their song output folder')
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=Path, default=OUTPUT_ROOT / 'jobs.json')
    parser.add_argument('--steps', type=int, choices=range(4, 101), default=50)
    parser.add_argument('--threads', type=int, choices=range(1, 9), default=6)
    parser.add_argument('--worker-id', default='main', help='Unique shard name; main keeps the legacy state/lock paths')
    parser.add_argument('--reference-seconds', type=float, default=8,
                        help='Initial excerpt of the approved voice reference (3..25s), preserving its pitch/timing')
    parser.add_argument('--only-id', action='append', default=[])
    parser.add_argument('--watch', '--wait-for-stems', dest='watch', action='store_true',
                        help='Wait in manifest order for validated separated WAVs; never download source audio')
    parser.add_argument('--poll-seconds', type=float, default=15)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--validate-only', action='store_true', help='Validate reference and jobs without loading any model')
    args = parser.parse_args()
    try:
        state_path, worker_lock_path = worker_paths(args.worker_id)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.poll_seconds <= 60:
        parser.error('--poll-seconds must be 1..60')
    if not 3 <= args.reference_seconds <= 25:
        parser.error('--reference-seconds must be 3..25')
    args.jobs = args.jobs.resolve()
    jobs = load_jobs(args.jobs)
    tracks = [track for track in jobs['tracks'] if not args.only_id or track['id'] in args.only_id]
    if not tracks:
        parser.error('No tracks matched --only-id')
    reference = project_path(jobs['reference'])
    reference_hash = jobs['reference_sha256']
    if args.validate_only:
        log('validated', tracks=len(tracks), reference=str(reference), reference_sha256=reference_hash,
            ready_stems=sum(project_path(track['vocals']).is_file() for track in tracks), conversion_run=False,
            worker_id=args.worker_id, state_path=str(state_path))
        return

    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = str(args.threads)
    os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1', HF_HUB_DISABLE_TELEMETRY='1',
                      LIBROSA_CACHE_DIR=str(ENGINE_ROOT / '.librosa-cache'), MPLCONFIGDIR=str(ENGINE_ROOT / '.matplotlib'))
    sys.path.insert(0, str(ENGINE_ROOT))
    import numpy as np
    import soundfile as sf
    import torch
    from filelock import FileLock
    import inference as seed

    if str(torch.__version__).split('+')[0] == '2.4.0':
        raise RuntimeError('Torch2.4.0 on Windows produced invalid CPU math; use the verified isolated2.4.1+cpu runtime')
    if torch.cuda.is_available() or seed.device.type != 'cpu':
        raise ValueError('This verified runner expects CPU inference')
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(2)
    torch.manual_seed(0)
    random.seed(0)
    np.random.seed(0)
    os.chdir(ENGINE_ROOT)
    reference_audio, reference_rate = sf.read(reference, dtype='float32', always_2d=True)
    reference_samples = min(len(reference_audio), round(reference_rate * args.reference_seconds))
    if reference_samples < reference_rate * 3 or not np.isfinite(reference_audio).all():
        raise ValueError('Approved reference needs at least3seconds of finite audio')
    reference_excerpt = ENGINE_ROOT / f'astra-reference-{reference_hash[:16]}-{args.reference_seconds:g}s.wav'
    # FLOAT WAV containers can carry changing PEAK timestamps. Key the cache by
    # exact decoded samples, not mutable container metadata.
    reference_excerpt_hash = hashlib.sha256(reference_audio[:reference_samples].tobytes()).hexdigest()
    # A valid excerpt is immutable and shared read-only by all shards. Only the
    # first worker writes it, under a short lease, using a worker/PID-unique temp.
    with FileLock(str(reference_excerpt) + '.lock', timeout=30):
        rewrite = True
        if reference_excerpt.is_file():
            try:
                cached, cached_rate = sf.read(reference_excerpt, dtype='float32', always_2d=True)
                rewrite = (cached_rate != reference_rate or cached.shape != reference_audio[:reference_samples].shape
                           or hashlib.sha256(cached.tobytes()).hexdigest() != reference_excerpt_hash)
            except (OSError, RuntimeError):
                rewrite = True
        if rewrite:
            reference_partial = excerpt_partial_path(reference_excerpt, args.worker_id)
            try:
                sf.write(reference_partial, reference_audio[:reference_samples], reference_rate, format='WAV', subtype='FLOAT')
                os.replace(reference_partial, reference_excerpt)
            finally:
                reference_partial.unlink(missing_ok=True)
    settings = {'engine': 'Seed-VC v1 SVC', 'source_commit': COMMIT, 'torch': str(torch.__version__),
                'torchaudio': str(seed.torchaudio.__version__), 'device': 'cpu', 'steps': args.steps,
                'threads': args.threads, 'fp16': False, 'f0_condition': True, 'auto_f0_adjust': False,
                'length_adjust': 1.0, 'semitone_shift': 0, 'inference_cfg_rate': 0.7, 'sample_rate': RATE,
                'channels': 1, 'subtype': 'FLOAT', 'reference_excerpt_seconds': reference_samples / reference_rate,
                'reference_excerpt_audio_sha256': reference_excerpt_hash, 'torch_sdpa_backend': 'default_fixed_torch241',
                'silence_policy': 'full source; mixer gates near-silent stem regions'}
    run_started = time.monotonic()
    total_audio_seconds = sum(float(track.get('source_duration', 0)) for track in tracks)
    state = {'status': 'starting', 'created_at': stamp(), 'settings': settings,
             'reference': str(reference), 'reference_sha256': reference_hash, 'tracks': {}, 'active': None,
             'total': len(tracks), 'pid': os.getpid(), 'worker_id': args.worker_id,
             'assigned_ids': [track['id'] for track in tracks]}
    conversion_args = SimpleNamespace(source='', target=str(reference_excerpt), output='', diffusion_steps=args.steps,
        length_adjust=1.0, inference_cfg_rate=0.7, f0_condition=True, auto_f0_adjust=False,
        semi_tone_shift=0, checkpoint=None, config=None, fp16=False)

    def save_state():
        state['updated_at'] = stamp()
        state['updated'] = state['updated_at']
        state['state'] = state['status']
        state['stage'] = (state.get('active') or {}).get('stage', state['status'])
        state['ready'] = sum(value.get('status') == 'ready' for value in state['tracks'].values())
        state['current_title'] = (state.get('active') or {}).get('title')
        if not state['current_title'] and state.get('pending'):
            state['current_title'] = next((track['title'] for track in tracks if track['id'] == state['pending'][0]), None)
        completed_audio = sum(float(value.get('source_duration', 0)) for value in state['tracks'].values())
        current_audio = float((state.get('active') or {}).get('processed_audio_seconds', 0))
        state['processed_audio_seconds'] = completed_audio + current_audio
        state['elapsed_seconds'] = time.monotonic() - run_started
        measured = [value['realtime_factor'] for value in state['tracks'].values() if value.get('realtime_factor')]
        current = state.get('active') or {}
        if current_audio > 0 and current.get('compute_started'):
            # Cached chunks are already complete, so only newly processed audio
            # contributes to speed estimation for this process.
            newly_processed = current_audio - float(current.get('resumed_audio_seconds', 0))
            if newly_processed > 0:
                measured.append((time.monotonic() - current['compute_started']) / newly_processed)
        state['estimated_seconds_remaining'] = (max(0, total_audio_seconds - state['processed_audio_seconds'])
            * (sum(measured) / len(measured))) if measured else None
        publish(state_path, state)

    def assert_reference_current():
        current = load_jobs(args.jobs)
        if current['reference_sha256'] != reference_hash or project_path(current['reference']) != reference:
            raise RuntimeError('Voice reference changed while converting; output is held and must be restarted')
        current_tracks = {track['id']: track for track in current['tracks']}
        for track in tracks:
            updated = current_tracks.get(track['id'], {})
            if any(updated.get(key) != track.get(key) for key in
                   ('source_sha256', 'vocals', 'accompaniment', 'output_dir', 'reference_sha256')):
                raise RuntimeError('Song inputs changed while converting; restart against the updated manifest')

    def stems_ready(track: dict) -> bool:
        directory = project_path(track['output_dir'])
        try:
            metadata = json.loads((directory / 'separation.json').read_text(encoding='utf-8'))
            return (metadata.get('status') == 'ready' and metadata.get('kind') == 'full_song'
                    and project_path(track['vocals']).is_file() and project_path(track['accompaniment']).is_file())
        except (OSError, json.JSONDecodeError):
            return False

    def validate_stems(track: dict) -> tuple[dict, str, int]:
        directory = project_path(track['output_dir'])
        metadata = json.loads((directory / 'separation.json').read_text(encoding='utf-8'))
        vocal = project_path(track['vocals'])
        backing = project_path(track['accompaniment'])
        vocal_info, backing_info = sf.info(vocal), sf.info(backing)
        if (vocal_info.samplerate != RATE or backing_info.samplerate != RATE
                or vocal_info.frames != backing_info.frames or vocal_info.frames != metadata['samples']):
            raise ValueError('Separated stem sample counts/rates disagree')
        if metadata.get('source_sha256') != track['source_sha256']:
            raise ValueError('Separation and job source hashes disagree')
        vocal_hash = sha256(vocal)
        if vocal_hash != metadata['outputs']['vocals']['sha256'] or sha256(backing) != metadata['outputs']['accompaniment']['sha256']:
            raise ValueError('Separated stem hash mismatch')
        peak, square_sum, values = 0.0, 0.0, 0
        for block in sf.blocks(vocal, blocksize=RATE * 10, dtype='float32', always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError('Separated vocal contains nonfinite values')
            peak = max(peak, float(np.abs(block).max()))
            square_sum += float(np.sum(block.astype('float64') ** 2))
            values += block.size
        rms = (square_sum / max(1, values)) ** 0.5
        if peak > 4 or rms > 1:
            raise ValueError(f'Separated vocal has implausible amplitude: peak={peak},rms={rms}')
        return metadata, vocal_hash, vocal_info.frames

    def reusable(directory: Path, vocal_hash: str, samples: int) -> dict | None:
        try:
            prior = json.loads((directory / 'conversion.json').read_text(encoding='utf-8'))
            output = directory / 'converted-vocals.wav'
            info = sf.info(output)
            prior_settings = {key: value for key, value in prior.get('settings', {}).items() if key != 'threads'}
            cache_settings = {key: value for key, value in settings.items() if key != 'threads'}
            if (prior.get('status') == 'ready' and prior_settings == cache_settings
                    and prior.get('reference_sha256') == reference_hash and prior.get('vocals_sha256') == vocal_hash
                    and info.samplerate == RATE and info.channels == 1 and info.frames == samples
                    and info.subtype == 'FLOAT' and sha256(output) == prior.get('output_sha256')):
                return prior
        except (OSError, RuntimeError, KeyError, json.JSONDecodeError):
            pass
        return None

    with FileLock(str(worker_lock_path), timeout=0):
        save_state()
        try:
            # Defer model loading until actual separated audio exists.
            models = None
            pending = list(tracks)
            while pending:
                assert_reference_current()
                made_progress = False
                locked_tracks = []
                for track in list(pending):
                    if not stems_ready(track):
                        if args.watch:
                            break  # Preserve the user's manifest order.
                        continue
                    directory = project_path(track['output_dir'])
                    with song_lease(directory) as acquired:
                        if not acquired:
                            locked_tracks.append(track['id'])
                            continue
                        made_progress = True
                        try:
                            metadata, vocal_hash, samples = validate_stems(track)
                            prior = None if args.force else reusable(directory, vocal_hash, samples)
                            if prior:
                                state['tracks'][track['id']] = prior
                                pending.remove(track)
                                log('reused', id=track['id'], output=str(directory / 'converted-vocals.wav'))
                                save_state()
                                continue
                            assert_reference_current()
                            if models is None:
                                state['status'] = 'loading_models'
                                save_state()
                                started = time.monotonic()
                                models = seed.load_models(conversion_args)
                                state['model_load_seconds'] = time.monotonic() - started
                                seed.load_models = lambda unused: models
                                log('model_ready', seconds=state['model_load_seconds'], threads=args.threads)
                            state['status'] = 'converting'
                            state['active'] = {'id': track['id'], 'title': track['title'], 'started_at': stamp(),
                                'duration_seconds': samples / RATE, 'completed_chunks': 0, 'stage': 'extracting_features',
                                'processed_audio_seconds': 0, 'compute_started': time.monotonic()}
                            publish(directory / 'conversion.json', {'status': 'converting', 'updated_at': stamp(),
                                'reference_sha256': reference_hash, 'vocals_sha256': vocal_hash, 'settings': settings})
                            save_state()
                            # .work never constitutes a ready asset. Only the final ready
                            # sidecar publishes the atomically replaced waveform.
                            work = directory / '.seed-vc-work'
                            work.mkdir(exist_ok=True)
                            conversion_args.source = str(project_path(track['vocals']))
                            conversion_args.output = str(work)
                            cache_settings = {key: value for key, value in settings.items() if key != 'threads'}
                            fingerprint = hashlib.sha256(json.dumps({'vocals': vocal_hash, 'reference': reference_hash,
                                'settings': cache_settings}, sort_keys=True).encode()).hexdigest()
                            conversion_args.chunk_directory = str(work / ('chunks-' + fingerprint[:20]))
                            conversion_args.chunk_fingerprint = fingerprint

                            def on_chunk(progress):
                                assert_reference_current()
                                state['active'].update(progress)
                                if progress['stage'] == 'resumed':
                                    state['active']['resumed_audio_seconds'] = progress['processed_audio_seconds']
                                    state['active']['compute_started'] = time.monotonic()
                                save_state()
                                log('chunk_progress', **state['active'])

                            conversion_args.on_chunk = on_chunk
                            seed_value = int(hashlib.sha256((track['id'] + reference_hash).encode()).hexdigest()[:8], 16)
                            torch.manual_seed(seed_value)
                            np.random.seed(seed_value)
                            started = time.monotonic()
                            log('track_start', id=track['id'], title=track['title'], source_seconds=samples / RATE, steps=args.steps)
                            seed.main(conversion_args)
                            seconds = time.monotonic() - started
                            resumed_audio_seconds = float(state['active'].get('resumed_audio_seconds', 0))
                            newly_converted_seconds = max(0, samples / RATE - resumed_audio_seconds)
                            raw = work / f'vc_{Path(conversion_args.source).stem}_{reference_excerpt.stem}_1.0_{args.steps}_0.7.wav'
                            audio, rate = sf.read(raw, dtype='float32', always_2d=True)
                            if rate != RATE or audio.shape[1] != 1 or not np.isfinite(audio).all():
                                raise ValueError('Converted audio must be finite mono 44.1 kHz')
                            peak = float(np.abs(audio).max())
                            rms = float(np.sqrt(np.mean(audio.astype('float64') ** 2)))
                            if peak > 1.5 or rms > 0.6:
                                raise ValueError(f'Converted vocal has implausible amplitude: peak={peak},rms={rms}')
                            original_samples = len(audio)
                            difference = samples - original_samples
                            # Only account for bounded STFT/vocoder frame rounding. A
                            # missing verse must fail rather than be padded with silence.
                            if abs(difference) > 2048:
                                raise ValueError(f'Conversion duration mismatch exceeds frame-rounding limit: {difference} samples')
                            if difference > 0:
                                audio = np.pad(audio, ((0, difference), (0, 0)))
                            elif difference < 0:
                                audio = audio[:samples]
                            assert_reference_current()
                            if sha256(project_path(track['vocals'])) != vocal_hash:
                                raise ValueError('Vocal stem changed during conversion')
                            output = directory / 'converted-vocals.wav'
                            partial = output.with_name(output.name + '.partial')
                            sf.write(partial, audio, RATE, format='WAV', subtype='FLOAT')
                            os.replace(partial, output)
                            result = {'status': 'ready', 'created_at': stamp(), 'id': track['id'], 'title': track['title'],
                                'engine': settings['engine'], 'steps': args.steps, 'seconds': seconds, 'settings': settings,
                                'source_duration': samples / RATE, 'source_samples': samples, 'samples': samples,
                                'sample_rate': RATE, 'channels': 1, 'raw_samples': original_samples,
                                'end_pad_samples': max(0, difference), 'end_trim_samples': max(0, -difference),
                                'realtime_factor': seconds / newly_converted_seconds if newly_converted_seconds >= 1 else None,
                                'resumed_audio_seconds': resumed_audio_seconds, 'newly_converted_audio_seconds': newly_converted_seconds,
                                'source_sha256': track['source_sha256'],
                                'vocals_sha256': vocal_hash, 'reference': str(reference), 'reference_sha256': reference_hash,
                                'output': str(output), 'output_sha256': sha256(output), 'peak': peak, 'rms': rms,
                                'pitch_policy': 'source F0 conditioning; no global pitch shift or automatic pitch adjustment',
                                'timing_policy': 'length_adjust=1; only bounded end trim/pad; no tempo change',
                                'separation_metadata': str(directory / 'separation.json')}
                            result['diffusion_steps'] = args.steps
                            result['worker_id'] = args.worker_id
                            result['chunk_cache'] = conversion_args.chunk_directory
                            publish(directory / 'conversion.json', result)
                            state['tracks'][track['id']] = result
                            state['active'] = None
                            pending.remove(track)
                            log('converted_vocals_ready', id=track['id'], seconds=seconds, realtime_factor=result['realtime_factor'], output=str(output))
                            save_state()
                        except BaseException as error:
                            if state.get('active') and state['active']['id'] == track['id']:
                                publish(directory / 'conversion.json', {
                                    'status': 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                                    'updated_at': stamp(), 'id': track['id'], 'worker_id': args.worker_id,
                                    'reference_sha256': reference_hash, 'settings': settings,
                                    'error_type': type(error).__name__, 'error': str(error)[:500]})
                            raise
                if pending:
                    waiting_status = 'waiting_for_track_lock' if all(track['id'] in locked_tracks for track in pending) else 'waiting_for_stems'
                    state['locked_tracks'] = locked_tracks
                    if not args.watch:
                        state.update(status=waiting_status, pending=[track['id'] for track in pending], active=None)
                        save_state()
                        log('waiting_for_stems', pending=state['pending'], restart_with_watch=True)
                        return
                    state.update(status=waiting_status, pending=[track['id'] for track in pending], active=None)
                    save_state()
                    if made_progress:
                        log('waiting_for_stems', pending=state['pending'])
                    time.sleep(args.poll_seconds)
            state.update(status='complete', active=None, pending=[])
            save_state()
            log('complete', tracks=len(tracks))
        except BaseException as error:
            state.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                         error_type=type(error).__name__, error=str(error)[:500])
            save_state()
            raise


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
