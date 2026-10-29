"""Prepared voice transitions reuse local audio without extending a performance."""
import asyncio
import json
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lameenc

from src.config import Config, TTSConfig
from src.module_transitions import ModuleTransitions, TRANSITION_LINES
from src.safety import SafetyGate
from src.tts import TTSEngine


def fixture_mp3(seconds=1):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96); encoder.set_in_sample_rate(24000); encoder.set_channels(1)
    return bytes(encoder.encode(b'\0' * int(48000 * seconds)) + encoder.flush())


class FakeEngine(TTSEngine):
    def __init__(self, output):
        config = TTSConfig(engine='mimo-tts', mimo_style='Saved character voice', pitch='+2Hz')
        config._api_keys = {'mimo-tts': 'secret-test-provider-key'}
        super().__init__(config, output)
        self.calls = []
        self.delay = 0
        self.audio = fixture_mp3()
        self.error = None

    async def synthesize(self, text, emotion=None):
        self.calls.append((text, emotion, self.config.model_copy(deep=True)))
        call_index = len(self.calls)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        path = self.output_dir / f'tts_fixture_{call_index}.mp3'
        path.write_bytes(self.audio)
        return '/audio/' + path.name


class ModuleTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = FakeEngine(self.root)
        self.pipeline = SimpleNamespace(tts=self.engine, result_queue=asyncio.Queue(maxsize=8),
                                        safety=SafetyGate(Config().safety))
        self.state = SimpleNamespace(active_character='astra', characters=SimpleNamespace(profiles={
            name: SimpleNamespace(voice=SimpleNamespace(enabled=True)) for name in ('astra', 'other')}))
        self.helper = ModuleTransitions(self.pipeline, self.state)

    async def asyncTearDown(self):
        await self.helper.close()
        self.temp.cleanup()

    async def test_uses_saved_voice_and_produces_valid_persistent_secret_free_clip(self):
        clip = await self.helper.prepare('singing', 'intro')
        self.assertIsNotNone(clip)
        self.assertGreater(clip['duration'], 1)
        self.assertLess(clip['duration'], 1.2)
        self.assertEqual(clip['profile_id'], 'astra')
        self.assertEqual(clip['emotion'], 'happy')
        text, emotion, config = self.engine.calls[0]
        self.assertEqual((text, emotion), (clip['text'], 'happy'))
        self.assertEqual(config.mimo_style, 'Saved character voice')
        self.assertEqual(config.pitch, '+2Hz')
        name = clip['audio_url'].removeprefix('/audio/')
        self.assertRegex(name, r'^module_transition_[a-f0-9]{64}\.mp3$')
        self.assertTrue((self.root / name).is_file())
        self.assertTrue(self.helper.matches_current(clip))
        self.assertNotIn('secret-test-provider-key', json.dumps(clip))
        self.assertEqual(list(self.root.glob('*.json')), [])

    async def test_cached_audio_survives_helper_restart_and_key_rotation(self):
        text = TRANSITION_LINES['story']['start'][0]
        with patch.object(ModuleTransitions, '_pick', return_value=text):
            first = await self.helper.prepare('story', 'start')
            self.engine.config._api_keys['mimo-tts'] = 'another-private-key'
            restarted = ModuleTransitions(self.pipeline, self.state)
            try:
                second = await restarted.prepare('story', 'start')
            finally:
                await restarted.close()
        self.assertEqual(first, second)
        self.assertEqual(len(self.engine.calls), 1)

    async def test_pitch_provider_style_and_pronunciation_controls_invalidate_cache(self):
        text = TRANSITION_LINES['singing']['end'][0]
        with patch.object(ModuleTransitions, '_pick', return_value=text):
            old = await self.helper.prepare('singing', 'end')
            clips = [old]
            for field, value in [('pitch', '+3Hz'), ('minimax_pitch', 2),
                                 ('mimo_style', 'A different saved voice'),
                                 ('pronunciations', {'唱完': '唱完啦'}), ('rate', '+3%')]:
                setattr(self.engine.config, field, value)
                clips.append(await self.helper.prepare('singing', 'end'))
            self.assertFalse(self.helper.matches_current(old))
        self.assertEqual(len({clip['audio_url'] for clip in clips}), len(clips))
        self.assertEqual(len(self.engine.calls), len(clips))

    async def test_profile_change_guard_and_per_profile_shuffle_bags(self):
        first = await self.helper.prepare('singing', 'start')
        self.state.active_character = 'other'
        self.assertFalse(self.helper.matches_current(first))
        other = await self.helper.prepare('singing', 'start')
        self.assertEqual(other['profile_id'], 'other')
        self.assertIn(('astra', 'singing', 'start'), self.helper._bags)
        self.assertIn(('other', 'singing', 'start'), self.helper._bags)

    async def test_saved_profile_voice_edit_invalidates_prepared_clip_before_engine_replacement(self):
        clip = await self.helper.prepare('story', 'start')
        self.state.characters.profiles['astra'].voice.pitch = 4
        self.assertFalse(self.helper.matches_current(clip))

    async def test_shuffle_cycles_all_three_lines_without_immediate_repeat(self):
        lines = [self.helper._pick('astra', 'singing', 'end') for _ in range(30)]
        for index in range(0, 30, 3):
            self.assertEqual(set(lines[index:index + 3]), set(TRANSITION_LINES['singing']['end']))
        self.assertTrue(all(left != right for left, right in zip(lines, lines[1:])))

    async def test_pair_preparation_is_concurrent_and_each_clip_is_bounded(self):
        both_started = asyncio.Event()
        started_count = 0
        original = self.engine.synthesize

        async def concurrent_synthesis(text, emotion=None):
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            # A serialized implementation cannot get the first call past this
            # barrier; test concurrency without a fragile wall-clock threshold.
            await asyncio.wait_for(both_started.wait(), timeout=.5)
            return await original(text, emotion)

        with patch.object(self.engine, 'synthesize', side_effect=concurrent_synthesis):
            pair = await self.helper.prepare_pair('story')
        self.assertIsNotNone(pair['intro']); self.assertIsNotNone(pair['outro'])
        self.assertTrue(both_started.is_set())
        self.assertEqual(len(self.engine.calls), 2)

    async def test_corrupt_cached_take_is_replaced_with_valid_audio(self):
        with patch.object(ModuleTransitions, '_pick', return_value=TRANSITION_LINES['singing']['start'][0]):
            first = await self.helper.prepare('singing', 'start')
            path = self.root / first['audio_url'].removeprefix('/audio/')
            path.write_bytes(b'invalid stale cached audio')
            second = await self.helper.prepare('singing', 'start')
        self.assertEqual(first['audio_url'], second['audio_url'])
        self.assertEqual(len(self.engine.calls), 2)
        self.assertGreater(second['duration'], 1)

    async def test_disabled_voice_missing_engine_and_unknown_module_skip_synthesis(self):
        self.state.characters.profiles['astra'].voice.enabled = False
        self.assertIsNone(await self.helper.prepare('story', 'start'))
        self.state.characters.profiles['astra'].voice.enabled = True
        self.engine.config.engine = 'none'
        self.assertIsNone(await self.helper.prepare('story', 'start'))
        self.pipeline.tts = None
        self.assertIsNone(await self.helper.prepare('story', 'end'))
        self.assertIsNone(await self.helper.prepare('not-a-module', 'start'))
        self.assertEqual(self.engine.calls, [])

    async def test_failures_return_none_with_sanitized_notice(self):
        self.engine.error = RuntimeError('secret-test-provider-key https://private.example')
        self.assertIsNone(await self.helper.prepare('singing', 'start'))
        notice = self.pipeline.result_queue.get_nowait()
        self.assertEqual(notice['type'], 'module_notice')
        self.assertNotIn('private', str(notice))
        self.assertNotIn('secret', str(self.helper.last_notice))
        self.assertEqual(list(self.root.glob('module_transition_*.mp3')), [])

    async def test_timeout_cancels_only_its_synthesis_and_returns_without_blocking(self):
        self.engine.delay = 1
        with patch('src.module_transitions.PREPARE_TIMEOUT_SECONDS', .02):
            started = time.monotonic()
            self.assertIsNone(await self.helper.prepare('story', 'end'))
            self.assertLess(time.monotonic() - started, .15)
        self.assertEqual(self.helper._tasks, set())
        self.assertEqual(list(self.root.glob('module_transition_*.mp3')), [])

    async def test_invalid_long_or_oversized_audio_never_enters_cache(self):
        for audio in (b'not-an-mp3', fixture_mp3(13), b'x' * (1024 * 1024 + 1)):
            with self.subTest(length=len(audio)):
                self.engine.audio = audio
                self.assertIsNone(await self.helper.prepare('story', 'start'))
        self.assertEqual(list(self.root.glob('module_transition_*.mp3')), [])

    async def test_cache_pruning_preserves_recent_and_nonowned_files(self):
        keep = self.root / ('module_transition_' + 'a' * 64 + '.mp3')
        old = self.root / ('module_transition_' + 'b' * 64 + '.mp3')
        unrelated = self.root / 'module_transition_user_recording.mp3'
        keep.write_bytes(fixture_mp3()); old.write_bytes(fixture_mp3()); unrelated.write_bytes(b'owned elsewhere')
        os.utime(old, (time.time() - 7200, time.time() - 7200))
        with patch('src.module_transitions.SOFT_CACHE_LIMIT', 2):
            self.assertTrue(self.helper._make_room(self.root, self.root / ('module_transition_' + 'c' * 64 + '.mp3')))
        self.assertTrue(keep.exists()); self.assertFalse(old.exists()); self.assertTrue(unrelated.exists())

    async def test_close_cancels_owned_preparation_without_closing_shared_engine(self):
        self.engine.delay = 10
        pending = asyncio.create_task(self.helper.prepare('singing', 'start'))
        await asyncio.sleep(.01)
        await self.helper.close()
        self.assertIsNone(await pending)
        self.assertIsNone(await self.helper.prepare('story', 'start'))
        self.assertEqual(self.engine.config.engine, 'mimo-tts')


if __name__ == '__main__':
    unittest.main()
