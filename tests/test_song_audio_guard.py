"""Song requests must wait for emitted chat playback, not only synthesis."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import lameenc

from src.config import Config
from src.pipeline import Pipeline


class SpeechDrainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = 'tts_1234567890_1234abcd.mp3'
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(24000)
        encoder.set_channels(1)
        encoder.set_bit_rate(96)
        (self.root / self.name).write_bytes(bytes(encoder.encode(b'\0' * 48000) + encoder.flush()))
        self.pipeline = Pipeline(Config())
        self.pipeline.tts = SimpleNamespace(jobs={}, output_dir=self.root)

    async def emit(self, at, url=None):
        with patch('src.pipeline.time.time', return_value=at):
            await self.pipeline._emit(type='audio_segment', audio_url=url or '/audio/' + self.name)

    async def test_completed_audio_and_cached_repeats_drain_in_sequence(self):
        await self.emit(100)
        with patch('src.pipeline.time.time', return_value=100.5):
            self.assertTrue(self.pipeline.speech_pending())
        await self.emit(100.2)
        with patch('src.pipeline.time.time', return_value=102):
            self.assertTrue(self.pipeline.speech_pending())
        with patch('src.pipeline.time.time', return_value=104):
            self.assertFalse(self.pipeline.speech_pending())

    async def test_progressive_job_waits_until_its_file_and_playback_finish(self):
        await self.emit(100, '/api/voice-stream/' + self.name)
        job = SimpleNamespace(done=False)
        self.pipeline.tts.jobs[self.name] = job
        with patch('src.pipeline.time.time', return_value=100.5):
            self.assertTrue(self.pipeline.speech_pending())
        job.done = True
        with patch('src.pipeline.time.time', return_value=104):
            self.assertFalse(self.pipeline.speech_pending())

    async def test_invalid_paths_are_ignored_and_bad_audio_cannot_kill_the_queue(self):
        await self.emit(100, '/audio/../../.env')
        self.assertEqual(len(self.pipeline._speech_queue), 0)
        (self.root / self.name).write_bytes(b'failed audio')
        await self.emit(100)
        with patch('src.pipeline.time.time', return_value=104):
            self.assertFalse(self.pipeline.speech_pending())
