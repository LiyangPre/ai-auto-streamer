"""Prepare aligned lossless vocal/accompaniment stems with isolated CPU Demucs.

Run with the verified isolated audio environment's Python. Downloads public model weights only; no
credentials, source uploads, song conversion, mixing or live application changes.
Completed tracks are reused only after source/settings/output hash validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

try:
    from .atomic_json import write_json_atomic
except ImportError:  # Direct script execution.
    from atomic_json import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
RATE = 44100


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def publish(path, value, *, best_effort=False):
    return write_json_atomic(path, value, best_effort=best_effort)


def log(event: str, **fields) -> None:
    print(json.dumps({'event': event, 'time': stamp(), **fields}, ensure_ascii=False), flush=True)


def ffmpeg_path(explicit: Path | None) -> Path:
    if explicit:
        binary = explicit.resolve()
        if not binary.is_file():
            raise FileNotFoundError('The supplied FFmpeg binary does not exist')
        return binary
    for environment in ('.venv-download', '.venv'):
        candidates = sorted((ROOT / environment / 'Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
        if candidates:
            return candidates[0]
    raise FileNotFoundError('Project imageio_ffmpeg binary missing; supply --ffmpeg')


def decode(binary: Path, source: Path, target: Path, *, offset: float = 0, seconds: float = 0) -> None:
    partial = target.with_name(target.name + '.partial')
    command = [str(binary), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    if offset:
        command += ['-ss', str(offset)]
    command += ['-i', str(source), '-vn']
    if seconds:
        command += ['-t', str(seconds)]
    command += ['-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'wav', str(partial)]
    completed = subprocess.run(command, capture_output=True, shell=False)
    if completed.returncode:
        raise RuntimeError('FFmpeg lossless decode failed: ' + completed.stderr.decode('utf-8', errors='replace')[-500:])
    os.replace(partial, target)


def validate_audio_duration(decoded_seconds: float, entry: dict, job: dict | None = None) -> dict:
    """Validate against an audio reference; video runtime can include silent tails."""
    if not math.isfinite(decoded_seconds) or decoded_seconds <= 0:
        raise ValueError('Decoded audio duration must be finite and positive')

    def positive(value):
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) and result > 0 else None

    expected = positive(entry.get('audio_duration'))
    reference = 'manifest.audio_duration' if expected is not None else None
    if expected is None and job:
        expected = positive(job.get('source_duration'))
        if expected is not None:
            reference = 'jobs.source_duration'
    if expected is None and entry.get('mp3'):
        try:
            from mutagen import MutagenError
            from mutagen.mp3 import MP3
        except ImportError:
            pass
        else:
            try:
                expected = positive(MP3(entry['mp3']).info.length)
                if expected is not None:
                    reference = 'local_mp3_audio_duration'
            except (OSError, ValueError, MutagenError):
                pass
    if expected is not None and abs(decoded_seconds - expected) > 2:
        raise ValueError('Decoded duration differs from the audio reference by more than 2 seconds')
    return {'decoded_audio_duration': decoded_seconds, 'expected_audio_duration': expected,
            'reference': reference or 'decoded_audio_only',
            'video_duration_metadata': entry.get('video_duration', entry.get('duration'))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/song-sources/download-results.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/songs-converted')
    parser.add_argument('--ffmpeg', type=Path)
    parser.add_argument('--threads', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--segment', type=float, default=7.8)
    parser.add_argument('--benchmark-seconds', type=float, default=12)
    parser.add_argument('--benchmark-offset', type=float, default=45)
    parser.add_argument('--benchmark-only', action='store_true')
    parser.add_argument('--only-bvid', action='append', default=[])
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Use already cached public model weights without network access')
    parser.add_argument('--attention', choices=('native', 'math'), default='native',
                        help='Native patched-runtime attention, or conservative math fallback')
    args = parser.parse_args()
    if not 0 <= args.benchmark_seconds <= 60 or args.benchmark_offset < 0:
        parser.error('Benchmark must be 0..60 seconds with a nonnegative offset')
    if not 1 <= args.segment <= 7.8:
        parser.error('HTDemucs segment must be 1..7.8 seconds')
    if args.benchmark_only and not args.benchmark_seconds:
        parser.error('--benchmark-only needs a positive --benchmark-seconds')

    # Set these before NumPy/Torch imports. Each song uses one model worker.
    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = str(args.threads)
    cache = ROOT / '.tools/demucs-cache'
    os.environ['HF_HOME'] = str(cache / 'huggingface')
    os.environ['HF_HUB_CACHE'] = str(cache / 'huggingface/hub')
    os.environ['TORCH_HOME'] = str(cache / 'torch')
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
    if args.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'

    import numpy as np
    import soundfile as sf
    import torch
    from demucs.api import Separator
    from filelock import FileLock

    if importlib.metadata.version('demucs') != '4.1.0':
        raise RuntimeError('This preparation script expects the verified Demucs 4.1.0 package')
    if os.name == 'nt' and torch.__version__.split('+')[0] == '2.4.0' and args.threads > 1:
        raise RuntimeError('Torch 2.4.0 Windows CPU corrupts multithreaded inference; use the verified 2.4.1 CPU patch or --threads 1')
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    # Torch 2.4.1 fixes the Windows CPU numerical regression. Retain an explicit
    # math-only option for diagnostics without changing installed packages.
    native_attention = args.attention == 'native'
    torch.backends.mha.set_fastpath_enabled(native_attention)
    torch.backends.cuda.enable_flash_sdp(native_attention)
    torch.backends.cuda.enable_mem_efficient_sdp(native_attention)
    torch.backends.cuda.enable_math_sdp(True)
    random.seed(0)
    torch.manual_seed(0)
    binary = ffmpeg_path(args.ffmpeg)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    entries = json.loads(args.manifest.read_text(encoding='utf-8'))
    try:
        jobs = json.loads((args.output / 'jobs.json').read_text(encoding='utf-8'))
        job_tracks = jobs.get('tracks', []) if isinstance(jobs, dict) else jobs
        jobs_by_bvid = {job.get('bvid', job.get('id')): job for job in job_tracks}
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        jobs_by_bvid = {}
    if args.only_bvid:
        entries = [entry for entry in entries if entry['bvid'] in args.only_bvid]
    if not entries:
        parser.error('No requested sources matched')
    for entry in entries:
        if not entry.get('ok') or not re.fullmatch(r'BV[a-zA-Z0-9]{10}', entry['bvid']):
            raise ValueError('Manifest contains an unsuccessful source or invalid BVID')
        source = Path(entry['original_audio']).resolve()
        if not source.is_relative_to((ROOT / 'data/song-sources').resolve()) or not source.is_file():
            raise ValueError('Source must exist inside data/song-sources')

    settings = {'engine': 'Demucs', 'version': '4.1.0', 'model': 'htdemucs', 'device': 'cpu',
                'torch_version': torch.__version__,
                'sample_rate': RATE, 'channels': 2, 'subtype': 'FLOAT', 'segment': args.segment,
                'overlap': 0.25, 'shifts': 1, 'jobs': 0, 'threads': args.threads,
                'accompaniment_method': 'sum_of_non_vocal_stems', 'independent_rescale': False,
                'torch_mha_fastpath': native_attention, 'torch_sdpa_backend': args.attention}
    state_path = args.output / 'separation-state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        state = {'created_at': stamp(), 'tracks': {}}
    state.update(settings=settings, status='starting', updated_at=stamp(), active=None, pid=os.getpid())
    state.pop('error_type', None)

    def save_state():
        state['updated_at'] = stamp()
        publish(state_path, state, best_effort=True)

    def valid_outputs(directory: Path, metadata: dict, expected_source: str) -> bool:
        if metadata.get('status') != 'ready' or not metadata.get('quality'):
            return False
        if metadata.get('settings') != settings or metadata.get('source_sha256') != expected_source:
            return False
        for stem in ('vocals', 'accompaniment'):
            output = directory / (stem + '.wav')
            try:
                info = sf.info(str(output))
                if info.samplerate != RATE or info.channels != 2 or info.frames != metadata['samples'] or info.subtype != 'FLOAT':
                    return False
                if sha256(output) != metadata['outputs'][stem]['sha256']:
                    return False
            except (OSError, RuntimeError, KeyError):
                return False
        return True

    def separate(separator, source_wav: Path, directory: Path, source_hash: str, label: str) -> dict:
        samples, sample_rate = sf.read(str(source_wav), dtype='float32', always_2d=True)
        if sample_rate != RATE or samples.shape[1] != 2 or not np.isfinite(samples).all():
            raise ValueError('Decoded source is not finite 44.1 kHz stereo audio')
        count = samples.shape[0]
        state['active'].update(stage='separating', samples=count, duration_seconds=count / RATE)
        save_state()
        started = time.perf_counter()
        # Use the tensor API and libsndfile FLOAT output to avoid Demucs CLI's
        # independent stem normalization/clamping. Keep all original time samples.
        with torch.inference_mode():
            _, stems = separator.separate_tensor(torch.from_numpy(samples.T.copy()), sr=RATE)
        vocal = stems.pop('vocals').numpy().T
        accompaniment = sum(stems.values()).numpy().T
        del stems
        source_rms = float(np.sqrt(np.mean(samples.astype('float64') ** 2)))
        source_peak = float(np.max(np.abs(samples)))
        reconstruction_rms = float(np.sqrt(np.mean((vocal.astype('float64') + accompaniment - samples) ** 2)))
        quality = {'source_rms': source_rms, 'source_peak': source_peak,
                   'reconstruction_error_rms': reconstruction_rms,
                   'reconstruction_error_ratio': reconstruction_rms / max(source_rms, 1e-8), 'stems': {}}
        # Reject unstable numerics rather than turning an invalid result down.
        for name, audio in (('vocals', vocal), ('accompaniment', accompaniment)):
            if audio.shape != (count, 2) or not np.isfinite(audio).all():
                raise ValueError(f'Separated {name} failed shape/finite validation')
            peak = float(np.max(np.abs(audio)))
            rms = float(np.sqrt(np.mean(audio.astype('float64') ** 2)))
            quality['stems'][name] = {'peak': peak, 'rms': rms}
            if peak > max(4.0, source_peak * 4) or rms > max(0.5, source_rms * 4):
                raise ValueError(f'Separated {name} has implausible amplitude: peak={peak:.5g}, rms={rms:.5g}')
        if reconstruction_rms > max(0.05, source_rms * 0.5):
            raise ValueError(f'Separated stems fail mixture reconstruction: rms_error={reconstruction_rms:.5g}')
        del samples
        outputs = {}
        for name, audio in (('vocals', vocal), ('accompaniment', accompaniment)):
            if audio.shape != (count, 2) or not np.isfinite(audio).all():
                raise ValueError(f'Separated {name} failed validation: shape={audio.shape}, '
                                 f'expected={(count, 2)}, nonfinite={int(np.size(audio) - np.isfinite(audio).sum())}')
            target = directory / (name + '.wav')
            partial = target.with_name(target.name + '.partial')
            sf.write(str(partial), audio, RATE, format='WAV', subtype='FLOAT')
            os.replace(partial, target)
            outputs[name] = {'path': str(target), 'sha256': sha256(target),
                             'peak': float(np.max(np.abs(audio))), 'bytes': target.stat().st_size}
        elapsed = time.perf_counter() - started
        metadata = {'status': 'ready', 'created_at': stamp(), 'kind': label, 'settings': settings,
                    'source_sha256': source_hash, 'decoded_source': str(source_wav), 'decoded_sha256': sha256(source_wav),
                    'samples': count, 'duration_seconds': count / RATE, 'separation_seconds': elapsed,
                    'realtime_factor': elapsed / (count / RATE), 'quality': quality, 'outputs': outputs}
        publish(directory / 'separation.json', metadata)
        log('stems_ready', kind=label, bvid=state['active']['bvid'], duration_seconds=count / RATE,
            separation_seconds=elapsed, realtime_factor=metadata['realtime_factor'],
            vocals=outputs['vocals']['path'], accompaniment=outputs['accompaniment']['path'])
        return metadata

    last_progress = 0.0

    def progress(details):
        nonlocal last_progress
        now = time.monotonic()
        if state.get('active') and now - last_progress >= 5:
            last_progress = now
            state['active']['progress'] = {key: details[key] for key in (
                'state', 'segment_offset', 'audio_length', 'model_idx_in_bag', 'models') if key in details}
            save_state()

    with FileLock(str(args.output / '.separation.lock'), timeout=0):
        save_state()
        state['status'] = 'loading_model'
        save_state()
        model_start = time.perf_counter()
        try:
            separator = Separator(model='htdemucs', device='cpu', shifts=1, split=True,
                                  segment=args.segment, overlap=0.25, jobs=0, progress=False, callback=progress)
        except BaseException as error:
            state.update(status='model_load_failed', error_type=type(error).__name__)
            save_state()
            raise
        if separator.samplerate != RATE or separator.audio_channels != 2:
            raise ValueError('Unexpected model sample rate/channels')
        state['model_load_seconds'] = time.perf_counter() - model_start
        state['model_assets'] = [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
                                 for p in cache.rglob('*') if p.is_file() and p.suffix in ('.safetensors', '.th', '.yaml')]
        log('model_ready', seconds=state['model_load_seconds'], threads=args.threads)
        state['status'] = 'running'
        save_state()
        try:
            for index, entry in enumerate(entries):
                bvid = entry['bvid']
                source = Path(entry['original_audio']).resolve()
                source_hash = sha256(source)
                directory = args.output / bvid
                directory.mkdir(parents=True, exist_ok=True)
                state['active'] = {'bvid': bvid, 'title': entry['requested_title'], 'index': index + 1,
                                   'total': len(entries), 'stage': 'validating', 'started_at': stamp()}
                save_state()
                metadata_path = directory / 'separation.json'
                try:
                    prior = json.loads(metadata_path.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    prior = {}
                if not args.benchmark_only and not args.force and valid_outputs(directory, prior, source_hash):
                    state['tracks'][bvid] = prior
                    log('reused', bvid=bvid, vocals=str(directory / 'vocals.wav'))
                    continue
                if index == 0 and args.benchmark_seconds:
                    benchmark_dir = directory / 'benchmark'
                    benchmark_dir.mkdir(parents=True, exist_ok=True)
                    benchmark_source = benchmark_dir / 'source.wav'
                    state['active']['stage'] = 'benchmark_decode'
                    save_state()
                    decode(binary, source, benchmark_source, offset=args.benchmark_offset, seconds=args.benchmark_seconds)
                    benchmark = separate(separator, benchmark_source, benchmark_dir, source_hash, 'benchmark')
                    benchmark['source_offset_seconds'] = args.benchmark_offset
                    publish(benchmark_dir / 'separation.json', benchmark)
                    state['benchmark'] = benchmark
                    if args.benchmark_only:
                        state.update(status='benchmark_complete', active=None)
                        save_state()
                        return
                state['active']['stage'] = 'decoding'
                save_state()
                decoded = directory / 'source.wav'
                decode(binary, source, decoded)
                info = sf.info(str(decoded))
                source_job = jobs_by_bvid.get(bvid)
                if source_job and source_job.get('source_sha256') not in (None, source_hash):
                    source_job = None
                duration_validation = validate_audio_duration(info.duration, entry, source_job)
                metadata = separate(separator, decoded, directory, source_hash, 'full_song')
                metadata.update(bvid=bvid, title=entry['requested_title'], source_path=str(source),
                                source_url=entry['source_url'], source_bytes=source.stat().st_size,
                                duration_validation=duration_validation)
                publish(metadata_path, metadata)
                state['tracks'][bvid] = metadata
                save_state()
            state.update(status='complete', active=None)
            save_state()
            log('complete', tracks=len(entries), state=str(state_path))
        except BaseException as error:
            state['status'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed'
            state['error_type'] = type(error).__name__
            save_state()
            raise


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
