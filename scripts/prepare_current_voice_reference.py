"""Prepare a local reference using Astra's exact saved MiMo voice description.

This makes one short TTS request, without broadcasting, changing settings or adding
role/style instructions. The reference is published only if the saved voice is
unchanged when synthesis completes. Earlier audition references are preserved.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imageio_ffmpeg
from src.api_settings import ConnectionStore, connection_path
from src.characters import library_path, load_library
from src.config import Config, PROJECT_ROOT
from src.studio import load_saved, settings_path
from src.tts import TTSEngine


TEXT = '你好呀，今天过得怎么样？我刚把小客厅收拾好，还给窗边的薄荷浇了水。等会儿克劳德和小鲸也要回来，我们可以一起听歌，聊聊天。你不用着急，慢慢说，我会认真听的。'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def main():
    cfg = Config.load()
    studio_path = settings_path('config.yaml')
    load_saved(cfg, studio_path)
    profile_path = library_path(studio_path)
    library = load_library(cfg, profile_path)
    profile = library.profiles['astra']
    profile.settings(library.scene).apply(cfg)
    style = profile.voice.mimo_style
    if profile.voice.provider != 'mimo-tts' or not style.strip():
        raise ValueError('Astra must have a saved MiMo voice description.')
    connections = ConnectionStore(connection_path(studio_path), cfg)
    connections.load()
    connections.apply(cfg)
    voice = cfg.tts.model_copy(deep=True)
    voice.engine = 'mimo-tts'
    voice.mimo_model = 'mimo-v2.5-tts-voicedesign'
    voice.mimo_style = style
    voice.timeout = 90
    destination = PROJECT_ROOT / 'data/voice-references'
    destination.mkdir(parents=True, exist_ok=True)
    mp3 = destination / 'astra-current-voice.mp3'
    wav = destination / 'astra-current-voice.wav'
    metadata_path = destination / 'astra-current-voice.json'
    pending_wav = destination / 'astra-current-voice.tmp.wav'
    engine = TTSEngine(voice, destination)
    try:
        started = time.perf_counter()
        url = await engine.synthesize(TEXT, 'normal')
        generation_ms = round((time.perf_counter() - started) * 1000)
        if not url:
            raise RuntimeError('Current voice reference request failed; check the private local API settings.')
        source = destination / url.rsplit('/', 1)[-1]
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-i', str(source),
                        '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(pending_wav)],
                       check=True, capture_output=True)
        with wave.open(str(pending_wav), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getnchannels() != 1 or audio.getframerate() != 24000 or audio.getsampwidth() != 2 or not 1 <= duration <= 30:
                raise ValueError('Generated reference must contain 1–30 seconds of mono 24 kHz PCM16.')
        latest = load_library(cfg, profile_path)
        latest_voice = latest.profiles['astra'].voice
        if (latest_voice.provider != profile.voice.provider or latest_voice.mimo_style != style
                or latest_voice.mimo_model != profile.voice.mimo_model):
            raise ValueError('Saved Astra voice changed during synthesis; the reference was not published.')
        metadata = {'path': str(wav), 'approved': True, 'candidate_id': 'current-profile', 'profile_id': 'astra',
                    'profile_revision': latest.revision, 'style': style, 'model': voice.mimo_model,
                    'name': 'Astra · current saved voice', 'reference_sha256': digest(pending_wav),
                    'source_sha256': digest(source), 'source_audition': str(mp3),
                    'duration_seconds': round(duration, 3), 'sample_rate': 24000, 'channels': 1,
                    'sample_width': 2, 'generation_ms': generation_ms, 'text': TEXT,
                    'voice_description_unmodified': True}
        pending_metadata = metadata_path.with_suffix('.json.tmp')
        pending_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        source.replace(mp3)
        pending_wav.replace(wav)
        pending_metadata.replace(metadata_path)
        proof_path = PROJECT_ROOT / 'data/qa/current-voice-reference.json'
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'ok': True, 'reference': str(wav), 'metadata': str(metadata_path),
                          'reference_sha256': metadata['reference_sha256'], 'profile_revision': latest.revision,
                          'duration_seconds': metadata['duration_seconds'], 'generation_ms': generation_ms,
                          'voice_description_unmodified': True}, ensure_ascii=False), flush=True)
    finally:
        pending_wav.unlink(missing_ok=True)
        await engine.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
