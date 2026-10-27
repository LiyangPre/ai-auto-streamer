"""Shared, bounded audio delivery for Edge, GLM, MiMo and MiniMax."""
import asyncio
import logging
import time
import uuid
import hashlib
import json
import re
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Optional

import edge_tts
import httpx

from .config import TTSConfig, PROJECT_ROOT
from .glm_tts import glm_audio_chunks
from .mimo_tts import mimo_audio_chunks

logger = logging.getLogger(__name__)


@dataclass
class VoiceStream:
    path: Path
    chunks: list[bytes] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    error: bool = False
    task: Optional[asyncio.Task] = None


class TTSEngine:
    """One synthesis per segment, shared by every connected player."""

    def __init__(self, config: TTSConfig, output_dir: str | Path = PROJECT_ROOT / "data/audio"):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, VoiceStream] = {}
        self.cache = OrderedDict()
        self._stream_slots = asyncio.Semaphore(2)
        self._http = None

    def _voice_params(self, emotion: Optional[str]) -> dict:
        """Resolve voice/rate/volume/pitch for an emotion (falls back to defaults)"""
        ov = (self.config.emotion_voice or {}).get(emotion or "", {}) if emotion and self.config.expressive else {}
        params = {"voice": self.config.voice, "rate": self.config.rate,
                  "volume": self.config.volume, "pitch": self.config.pitch}
        # Emotion adds a small inflection to the selected voice, never replaces
        # its timbre or jumps back to the old high-pitched preset.
        for name, suffix, bound in (("rate", "%", 4), ("pitch", "Hz", 2), ("volume", "%", 3)):
            base = int(params[name].removesuffix(suffix))
            delta = int(ov.get(name, "0" + suffix).removesuffix(suffix))
            params[name] = f"{base + max(-bound, min(bound, delta)):+d}{suffix}"
        return params

    def _cache_key(self, text, params, config=None, emotion=None):
        cfg = config or self.config
        provider = [cfg.engine, cfg.glm_voice, cfg.minimax_model, cfg.minimax_voice, cfg.minimax_pitch,
                    cfg.mimo_voice, cfg.mimo_style, cfg.expressive, emotion if cfg.expressive else None]
        return hashlib.sha256(json.dumps([text.strip(), params, provider], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _spoken_text(self, text):
        # Keep provider control tags and Markdown out of viewer speech.
        text = re.sub(r"<[^>]*>", "", text)
        text = re.sub(r"\((?:laughs|chuckle|breath|pant|groans|sighs|inhale|exhale)\)", "", text, flags=re.I)
        text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
        text = re.sub(r"https?://\S+", "链接", text)
        text = re.sub(r"[*`#]", "", text)
        text = re.sub(r"[!！]{2,}", "！", text)
        text = re.sub(r"[?？]{2,}", "？", text)
        for name, pronunciation in sorted(self.config.pronunciations.items(), key=lambda item: -len(item[0])):
            if name and pronunciation:
                text = re.sub(re.escape(name), lambda _: pronunciation, text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def provider_ready(config):
        env = {"glm-tts": config.glm_api_key_env, "minimax": config.minimax_api_key_env,
               "mimo-tts": config.mimo_api_key_env}.get(config.engine)
        return env is None or bool(os.environ.get(env, "").strip())

    async def _audio_chunks(self, text, params, config, emotion):
        if config.engine == "mimo-tts":
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=config.timeout)
            async for chunk in mimo_audio_chunks(self._http, text, config,
                                                 os.environ.get(config.mimo_api_key_env, "").strip()):
                yield chunk
            return
        if config.engine == "glm-tts":
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=config.timeout)
            async for chunk in glm_audio_chunks(self._http, text, config,
                                                os.environ.get(config.glm_api_key_env, "").strip()):
                yield chunk
            return
        if config.engine != "minimax":
            async for part in edge_tts.Communicate(text=text, **params).stream():
                if part["type"] == "audio":
                    yield part["data"]
            return
        key = os.environ.get(config.minimax_api_key_env, "").strip()
        if not key:
            raise ValueError("MiniMax voice key is not configured")
        # MiniMax pitch uses semitones; Edge's Hz slider is deliberately separate.
        body = {"model": config.minimax_model, "text": text, "stream": True,
                "stream_options": {"exclude_aggregated_audio": True},
                "voice_setting": {"voice_id": config.minimax_voice,
                    "speed": 1 + int(config.rate.rstrip("%")) / 100,
                    "vol": 1 + int(config.volume.rstrip("%")) / 100,
                    "pitch": config.minimax_pitch,
                    "emotion": emotion if config.expressive and emotion in {"happy", "sad", "angry", "surprised"} else "calm"},
                "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
                "language_boost": "Chinese"}
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=config.timeout)
        completed = False
        received = False
        async with self._http.stream("POST", "https://api.minimax.cn/v1/t2a_v2",
                                    headers={"Authorization": f"Bearer {key}"}, json=body,
                                    timeout=config.timeout) as response:
            if response.status_code != 200:
                raise ValueError(f"MiniMax voice HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    if line.lstrip().startswith("{"):
                        raise ValueError("MiniMax returned a non-stream response")
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                event = json.loads(payload)
                if (event.get("base_resp") or {}).get("status_code", 0) != 0:
                    raise ValueError("MiniMax voice request was rejected")
                data = event.get("data") or {}
                if data.get("status") == 2:
                    # Some endpoints still include the full aggregate at the end.
                    # Do not play all previously received audio a second time.
                    if not received and data.get("audio"):
                        yield bytes.fromhex(data["audio"])
                    completed = True
                    break
                if data.get("audio"):
                    received = True
                    yield bytes.fromhex(data["audio"])
        if not completed:
            raise ValueError("MiniMax audio stream ended before completion")

    def _cached(self, key):
        path = self.cache.get(key)
        if path and path.exists():
            self.cache.move_to_end(key)
            return f"/audio/{path.name}"
        self.cache.pop(key, None)
        return None

    def _remember(self, key, path):
        self.cache[key] = path
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)

    async def create_stream(self, text, emotion=None, on_first_audio=None, on_error=None):
        """Start one shared synthesis; every browser reads the same audio bytes."""
        text = self._spoken_text(text)
        if not text:
            return None
        config = self.config.model_copy(deep=True)
        params = self._voice_params(emotion)
        key = self._cache_key(text, params, config, emotion)
        cached = self._cached(key)
        if cached:
            if on_first_audio:
                on_first_audio()
            return cached
        # Keep in-memory audio bounded; completed files remain on disk for replay.
        for token, job in list(self.jobs.items()):
            if job.done and len(self.jobs) >= 24:
                del self.jobs[token]
        if len(self.jobs) >= 32:
            url = await self.synthesize(text, emotion)
            if url and on_first_audio:
                on_first_audio()
            elif not url and on_error:
                await on_error()
            return url
        filename = f"tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
        job = VoiceStream(self.output_dir / filename)
        self.jobs[filename] = job
        deadline = config.timeout

        async def produce():
            first = True
            total = 0
            with job.path.open("wb") as output:
                async for chunk in self._audio_chunks(text, params, config, emotion):
                    total += len(chunk)
                    if total > 2 * 1024 * 1024:
                        raise ValueError("Voice segment exceeds the streaming buffer limit")
                    output.write(chunk)
                    job.chunks.append(chunk)
                    job.changed.set()
                    if first:
                        first = False
                        if on_first_audio:
                            on_first_audio()
            if first:
                raise ValueError("No speech audio returned")
            self._remember(key, job.path)

        async def run():
            try:
                async with self._stream_slots:
                    await asyncio.wait_for(produce(), timeout=deadline)
            except asyncio.CancelledError:
                job.error = True
                job.path.unlink(missing_ok=True)
                raise
            except Exception:
                job.error = True
                job.path.unlink(missing_ok=True)
                logger.warning("Progressive voice synthesis failed")
                if on_error:
                    await on_error()
            finally:
                job.done = True
                job.changed.set()

        job.task = asyncio.create_task(run())
        return f"/api/voice-stream/{filename}"

    async def stream_bytes(self, filename):
        job = self.jobs[filename]
        index = 0
        while True:
            while index < len(job.chunks):
                chunk = job.chunks[index]
                index += 1
                yield chunk
            if job.done:
                return
            job.changed.clear()
            if index < len(job.chunks) or job.done:
                continue
            await job.changed.wait()

    async def close(self):
        tasks = [job.task for job in self.jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._http:
            await self._http.aclose()
            self._http = None

    async def synthesize(self, text: str, emotion: Optional[str] = None) -> Optional[str]:
        """Synthesize text to audio file, returns web URL"""
        text = self._spoken_text(text)
        if not text.strip():
            return None
        params = self._voice_params(emotion)
        config = self.config.model_copy(deep=True)
        key = self._cache_key(text, params, config, emotion)
        cached = self._cached(key)
        if cached:
            return cached

        output_file = self.output_dir / f"tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
        try:
            if config.engine in {"minimax", "glm-tts", "mimo-tts"}:
                async def save_cloud_audio():
                    total = 0
                    with output_file.open("wb") as output:
                        async for chunk in self._audio_chunks(text, params, config, emotion):
                            total += len(chunk)
                            if total > 2 * 1024 * 1024:
                                raise ValueError("Voice segment exceeds the buffer limit")
                            output.write(chunk)
                async with self._stream_slots:
                    await asyncio.wait_for(save_cloud_audio(), timeout=config.timeout)
            else:
                communicate = edge_tts.Communicate(text=text, **params)
                await asyncio.wait_for(communicate.save(str(output_file)), timeout=config.timeout)
            if not output_file.exists() or output_file.stat().st_size == 0:
                raise ValueError("No speech audio returned")
            self._remember(key, output_file)

            logger.debug(f"TTS generated: {output_file} (emotion={emotion}, provider={config.engine})")
            # Return a web URL matching the /audio static mount, not a fs path
            return f"/audio/{output_file.name}"

        except asyncio.CancelledError:
            output_file.unlink(missing_ok=True)
            raise
        except Exception:
            output_file.unlink(missing_ok=True)
            logger.error("TTS synthesis failed; check voice provider and credentials")
            return None

    async def synthesize_stream(
        self, text: str
    ) -> AsyncGenerator[bytes, None]:
        """Synthesize text to audio stream"""
        if not text.strip():
            return

        try:
            config = self.config.model_copy(deep=True)
            async for chunk in self._audio_chunks(self._spoken_text(text), self._voice_params(None), config, None):
                yield chunk

        except Exception:
            logger.error("TTS stream failed; check voice provider and credentials")

    async def get_voices(self) -> list:
        """Get available voices"""
        try:
            voices = await edge_tts.list_voices()
            return [v for v in voices if v["Locale"].startswith("zh-")]
        except Exception as e:
            logger.error(f"Failed to get voices: {e}")
            return []

    def cleanup_old_files(self, max_age_seconds: int = 3600):
        """Clean up old audio files"""
        try:
            current_time = time.time()
            for file in self.output_dir.glob("tts_*.mp3"):
                file_age = current_time - file.stat().st_mtime
                if file_age > max_age_seconds:
                    file.unlink()
                    logger.debug(f"Cleaned up old file: {file}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
