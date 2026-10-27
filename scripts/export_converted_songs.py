"""Mix completed offline vocal conversions with their aligned original accompaniment."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import wave

import imageio_ffmpeg
from mutagen.mp3 import MP3
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RATE = 44100


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_ffmpeg(arguments):
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner',
                             '-loglevel', 'error', '-y', *arguments], capture_output=True)
    if result.returncode:
        raise RuntimeError('Audio export failed: ' + result.stderr.decode('utf-8', errors='replace')[-800:])
    return result.stdout


def mono(path):
    data = run_ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', str(RATE), '-f', 'f32le', 'pipe:1'])
    audio = np.frombuffer(data, dtype='<f4')
    if not audio.size or not np.isfinite(audio).all() or np.max(np.abs(audio)) > 8:
        raise ValueError('Invalid or implausible audio levels; refusing to export.')
    return audio


def align_and_level(original, converted):
    if abs(len(original) - len(converted)) > RATE * .1:
        raise ValueError('Converted vocals drift by more than 0.1 seconds.')
    converted = np.pad(converted[:len(original)], (0, max(0, len(original) - len(converted))))
    frame = RATE // 25
    padded = np.pad(original, (0, (-len(original)) % frame))
    rms = np.sqrt(np.mean(padded.reshape(-1, frame).astype(np.float64) ** 2, axis=1))
    threshold = max(float(np.percentile(rms, 95)) * .0063, 1e-5)
    envelope = np.zeros(len(rms))
    release = 0.
    for index, value in enumerate(rms):
        release = 1. if value >= threshold else release * .65
        envelope[index] = release
    mask = np.interp(np.arange(len(original)), np.arange(len(rms)) * frame + frame / 2,
                     envelope, left=envelope[0], right=envelope[-1]).astype(np.float32)
    active = mask > .95
    if not np.any(active):
        raise ValueError('Source vocal stem contains no usable voice.')
    source_rms = float(np.sqrt(np.mean(original[active].astype(np.float64) ** 2)))
    target_rms = float(np.sqrt(np.mean(converted[active].astype(np.float64) ** 2)))
    if target_rms < 1e-7:
        raise ValueError('Conversion contains no audible voice.')
    gain = float(np.clip(source_rms / target_rms, .5, 2.))
    audio = converted * mask * gain
    peak = float(np.max(np.abs(audio)))
    if peak > .98:
        audio *= .98 / peak
    return audio, {'gain': gain, 'source_vocal_rms': source_rms,
                   'converted_vocal_rms': target_rms, 'quiet_gap_gate': True,
                   'pitch_shift': 0, 'time_stretch': False}


def export_track(track, jobs, required_steps=None):
    directory = Path(track['output_dir'])
    metadata_path = directory / 'conversion.json'
    if not metadata_path.is_file():
        return None
    conversion = json.loads(metadata_path.read_text(encoding='utf-8'))
    if conversion.get('status') != 'ready':
        return None
    steps = conversion.get('diffusion_steps', conversion.get('steps', conversion.get('settings', {}).get('steps')))
    if required_steps is not None and steps != required_steps:
        return None
    if conversion.get('reference_sha256') != jobs['reference_sha256']:
        raise ValueError('Conversion uses a different voice reference.')
    converted_path = directory / 'converted-vocals.wav'
    if 'source_sha256' in track:
        if (conversion.get('source_sha256') != track['source_sha256']
                or conversion.get('vocals_sha256') != digest(track['vocals'])
                or conversion.get('output_sha256') != digest(converted_path)):
            raise ValueError('Converted audio or its source no longer matches verified metadata.')
    mix_path, guide_path = directory / 'astra-song.mp3', directory / 'converted-vocals.mp3'
    export_path = directory / 'export.json'
    signature = hashlib.sha256(json.dumps([digest(converted_path), digest(track['vocals']),
                                           digest(track['accompaniment']), jobs['reference_sha256'],
                                           steps, track.get('source_duration'), conversion.get('source_samples'),
                                           conversion.get('samples'), 'mix-v3'], sort_keys=True).encode()).hexdigest()
    if export_path.is_file():
        previous = json.loads(export_path.read_text(encoding='utf-8'))
        if (previous.get('signature') == signature and mix_path.is_file() and guide_path.is_file()
                and previous.get('mix_sha256') == digest(mix_path)
                and previous.get('guide_sha256') == digest(guide_path)):
            return previous
    original, converted = mono(track['vocals']), mono(converted_path)
    expected = track.get('source_duration')
    if expected and abs(len(original) / RATE - float(expected)) > .2:
        raise ValueError('Stem is shorter or longer than the complete source song.')
    if 'source_sha256' in track and (conversion.get('samples') != len(converted)
                                    or conversion.get('source_samples') != len(original)):
        raise ValueError('Converted waveform sample counts do not match the verified complete song.')
    leveled, levels = align_and_level(original, converted)
    aligned = directory / 'aligned-vocals.wav'
    with wave.open(str(aligned), 'wb') as audio:
        audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        audio.writeframes((leveled * 32767).astype('<i2').tobytes())
    duration = len(original) / RATE
    mix_temp, guide_temp = directory / 'mix.partial.mp3', directory / 'guide.partial.mp3'
    run_ffmpeg(['-i', str(track['accompaniment']), '-i', str(aligned), '-filter_complex',
                '[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=false:latency=true',
                '-t', str(duration), '-ar', str(RATE), '-ac', '2', '-c:a', 'libmp3lame', '-b:a', '192k', str(mix_temp)])
    run_ffmpeg(['-i', str(aligned), '-ar', str(RATE), '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '96k', str(guide_temp)])
    mix_duration, guide_duration = MP3(mix_temp).info.length, MP3(guide_temp).info.length
    if abs(mix_duration - guide_duration) > .05 or abs(mix_duration - duration) > .1:
        raise ValueError('MP3 mix and mouth guide are not aligned.')
    mix_temp.replace(mix_path)
    guide_temp.replace(guide_path)
    result = {key: track[key] for key in ('id', 'bvid', 'title', 'source_url')}
    result.update(ready=True, signature=signature, mix_mp3=str(mix_path), vocals_mp3=str(guide_path),
                  mix_sha256=digest(mix_path), guide_sha256=digest(guide_path),
                  duration_seconds=round(mix_duration, 6), levels=levels,
                  conversion={'engine': 'Seed-VC', 'model': 'v1-SVC-44k',
                              'diffusion_steps': conversion.get('diffusion_steps', conversion.get('steps')),
                              'f0_condition': True, 'auto_f0_adjust': False, 'length_adjust': 1.,
                              'semitones': 0, 'fp16': False})
    publish(export_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=Path, default=ROOT / 'data/songs-converted/jobs.json')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--steps', type=int, choices=range(4, 101), default=50)
    args = parser.parse_args()
    while True:
        jobs = json.loads(args.jobs.read_text(encoding='utf-8'))
        reference = json.loads(Path(jobs['reference_metadata']).read_text(encoding='utf-8'))
        if not reference.get('approved') or digest(jobs['reference']) != jobs['reference_sha256']:
            raise ValueError('Approved reference is missing or changed.')
        tracks = [export_track(track, jobs, required_steps=args.steps) for track in jobs['tracks']]
        result = {key: jobs[key] for key in ('voice_candidate', 'reference', 'reference_metadata', 'reference_sha256')}
        result.update(tracks=[track for track in tracks if track], ready=sum(track is not None for track in tracks), total=len(tracks))
        publish(args.jobs.with_name('prepared.json'), result)
        print(json.dumps({'ready': result['ready'], 'total': result['total']}, ensure_ascii=False), flush=True)
        if not args.watch or result['ready'] == result['total']:
            break
        time.sleep(10)


if __name__ == '__main__':
    main()
