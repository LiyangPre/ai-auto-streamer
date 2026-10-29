"""Audience wait speech is bounded, voice-matched, and never races the answer."""
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.config import Config
from src.llm import CallParams, LLMError
from src.module_transitions import ModuleTransitions, WAIT_LINE
from src.pipeline import Pipeline
from tests.test_module_transitions import FakeEngine


CLIP = {
    'text': WAIT_LINE,
    'audio_url': '/audio/module_transition_' + 'a' * 64 + '.mp3',
    'duration': 3,
    'profile_id': 'astra',
    'voice_signature': 'saved-voice',
}


class ControlledLLM:
    def __init__(self, *, ready=False, error=None):
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.error = error
        if ready:
            self.release.set()

    async def chat(self, message, params):
        self.entered.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return '等到你啦，我们接着聊。', 'happy'

    async def chat_stream(self, message, params):
        text, emotion = await self.chat(message, params)
        yield text, emotion


class ControlledTransitions:
    def __init__(self, *, ready=True):
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0
        self.current = True
        if ready:
            self.release.set()

    async def prepare_wait(self):
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
            return dict(CLIP)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    def matches_current(self, clip):
        return self.current


class DelayNoticeTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self, *, llm_ready=False, prepare_ready=True):
        config = Config()
        config.reply_interval.enabled = False
        config.tts.progressive_audio = False
        config.llm.delay_notice_after = .05
        config.llm.delay_notice_cooldown = 45
        pipeline = Pipeline(config)
        pipeline.output_clients.add(object())
        pipeline.tts = SimpleNamespace(synthesize=AsyncMock(return_value='/audio/tts_100_12345678.mp3'))
        pipeline.llm = ControlledLLM(ready=llm_ready)
        pipeline.transitions = ControlledTransitions(ready=prepare_ready)
        return pipeline

    def events(self, pipeline):
        return [pipeline.result_queue.get_nowait() for _ in range(pipeline.result_queue.qsize())]

    async def wait_for_notice(self, pipeline):
        events = []
        async with asyncio.timeout(1):
            while True:
                event = await pipeline.result_queue.get()
                events.append(event)
                if event.get('source') == 'delay_notice':
                    return events

    async def test_one_approved_notice_precedes_the_real_reply_audio(self):
        for streaming in (False, True):
            with self.subTest(stream=streaming):
                pipeline = self.pipeline()
                task = asyncio.create_task(pipeline._reply('测试弹幕', CallParams(), stream=streaming))
                try:
                    events = await self.wait_for_notice(pipeline)
                    notice = events[-1]
                    self.assertFalse(task.done(), 'the model must still be pending when wait speech starts')
                    self.assertEqual(notice['text'], WAIT_LINE)
                    self.assertEqual(notice['audio_url'], CLIP['audio_url'])
                    self.assertTrue(pipeline.safety.allows_output(notice['text']))
                    self.assertIsNone(pipeline.metrics['first_text_ms'], 'wait speech is not an AI answer')
                    await asyncio.sleep(.07)
                    pipeline.llm.release.set()
                    reply = await asyncio.wait_for(task, .5)
                    events += self.events(pipeline)
                    audio = [event for event in events if event['type'] == 'audio_segment']
                    self.assertEqual(reply, '等到你啦，我们接着聊。')
                    self.assertEqual([event['text'] for event in audio], [WAIT_LINE, reply])
                    self.assertEqual(len([event for event in events if event.get('source') == 'delay_notice']), 1)
                    self.assertEqual(pipeline.metrics['responses'], 1)
                    self.assertEqual(pipeline.tts.synthesize.await_count, 1,
                                     'wait audio was prepared separately, not synthesized twice')
                    self.assertEqual(len(pipeline._speech_queue), 2,
                                     'automatic songs must wait for both spoken clips to finish')
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_fast_reply_cancels_preparation_and_never_emits_a_late_notice(self):
        pipeline = self.pipeline(prepare_ready=False)
        task = asyncio.create_task(pipeline._reply('测试弹幕', CallParams(), stream=True))
        try:
            await asyncio.wait_for(pipeline.transitions.entered.wait(), .5)
            pipeline.llm.release.set()
            await asyncio.wait_for(task, .5)
            self.assertTrue(pipeline.transitions.cancelled.is_set())
            pipeline.transitions.release.set()
            await asyncio.sleep(.07)
            events = self.events(pipeline)
            self.assertFalse([event for event in events if event.get('source') == 'delay_notice'])
            self.assertFalse(pipeline._is_replying)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_immediate_reply_does_not_add_a_notice(self):
        pipeline = self.pipeline(llm_ready=True)
        await pipeline._reply('测试弹幕', CallParams(), stream=False)
        self.assertFalse([event for event in self.events(pipeline) if event.get('source') == 'delay_notice'])
        self.assertEqual(pipeline._last_delay_notice, float('-inf'))

    async def test_disabled_no_output_no_voice_and_cooldown_skip_preparation(self):
        for condition in ('disabled', 'no_output', 'no_voice', 'cooldown'):
            with self.subTest(condition=condition):
                pipeline = self.pipeline()
                if condition == 'disabled':
                    pipeline.config.llm.delay_notice_enabled = False
                elif condition == 'no_output':
                    pipeline.output_clients.clear()
                    pipeline.ws_clients.add(object())  # A controller alone is not an audible output.
                elif condition == 'no_voice':
                    pipeline.tts = None
                else:
                    pipeline._last_delay_notice = time.monotonic()
                self.assertIsNone(pipeline._start_delay_notice())
                self.assertEqual(pipeline.transitions.calls, 0)

    async def test_cooldown_prevents_repeated_wait_speech_across_replies(self):
        pipeline = self.pipeline()
        state = pipeline._start_delay_notice()
        await asyncio.wait_for(state['task'], .5)
        self.assertTrue(state['spoken'])
        self.assertIsNone(pipeline._start_delay_notice())
        self.assertEqual(pipeline.transitions.calls, 1)
        pipeline._last_delay_notice -= 46
        next_state = pipeline._start_delay_notice()
        await asyncio.wait_for(next_state['task'], .5)
        self.assertTrue(next_state['spoken'])

    async def test_error_preserves_wait_audio_and_does_not_claim_an_answer(self):
        pipeline = self.pipeline()
        pipeline.llm.error = LLMError('Provider request timed out')
        task = asyncio.create_task(pipeline._reply('测试弹幕', CallParams(), stream=True))
        try:
            events = await self.wait_for_notice(pipeline)
            pipeline.llm.release.set()
            reply = await asyncio.wait_for(task, .5)
            events += self.events(pipeline)
            error = next(event for event in events if event['type'] == 'response_error')
            self.assertTrue(error['notice_spoken'], 'the frontend must let the wait line finish')
            self.assertEqual(reply, '')
            self.assertFalse([event for event in events if event['type'] in {'response', 'response_start', 'response_end'}])
            self.assertEqual(pipeline.metrics['responses'], 0)
            self.assertIsNone(pipeline.metrics['first_text_ms'])
            pipeline.tts.synthesize.assert_not_awaited()
            self.assertFalse(pipeline._is_replying)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancelling_a_reply_cancels_notice_and_its_voice_preparation(self):
        pipeline = self.pipeline(prepare_ready=False)
        task = asyncio.create_task(pipeline._reply('测试弹幕', CallParams(), stream=True))
        await asyncio.wait_for(pipeline.transitions.entered.wait(), .5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, .5)
        self.assertTrue(pipeline.transitions.cancelled.is_set())
        self.assertFalse(pipeline._is_replying)
        self.assertFalse([event for event in self.events(pipeline) if event.get('source') == 'delay_notice'])
        self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == 'reply-delay-notice'])

    async def test_no_wait_speech_during_a_story_or_song_including_a_stage_change(self):
        for module in ('story', 'singing'):
            with self.subTest(module=module):
                pipeline = self.pipeline()
                pipeline.stage.acquire(module)
                self.assertIsNone(pipeline._start_delay_notice())
                pipeline.stage.release(module)
                state = pipeline._start_delay_notice()
                pipeline.stage.acquire(module)
                await asyncio.wait_for(state['task'], .5)
                self.assertFalse(state['spoken'])
                self.assertTrue(pipeline.result_queue.empty())

    async def test_old_profile_or_voice_clip_is_discarded_before_broadcast(self):
        pipeline = self.pipeline(prepare_ready=False)
        state = pipeline._start_delay_notice()
        await asyncio.wait_for(pipeline.transitions.entered.wait(), .5)
        pipeline.transitions.current = False
        pipeline.transitions.release.set()
        await asyncio.wait_for(state['task'], .5)
        self.assertFalse(state['spoken'])
        self.assertTrue(pipeline.result_queue.empty())

    async def test_output_disconnected_during_preparation_discards_wait_speech(self):
        pipeline = self.pipeline(prepare_ready=False)
        state = pipeline._start_delay_notice()
        await asyncio.wait_for(pipeline.transitions.entered.wait(), .5)
        pipeline.output_clients.clear()
        pipeline.transitions.release.set()
        await asyncio.wait_for(state['task'], .5)
        self.assertFalse(state['spoken'])
        self.assertTrue(pipeline.result_queue.empty())

    async def test_local_review_blocks_even_a_prepared_wait_clip(self):
        pipeline = self.pipeline()
        pipeline.safety.approve = AsyncMock(return_value=False)
        state = pipeline._start_delay_notice()
        await asyncio.wait_for(state['task'], .5)
        self.assertFalse(state['spoken'])
        self.assertTrue(pipeline.result_queue.empty())
        pipeline.safety.approve.assert_awaited_once_with(WAIT_LINE)

    async def test_stage_voice_and_output_are_rechecked_after_an_async_review(self):
        for change in ('voice', 'stage', 'output'):
            with self.subTest(change=change):
                pipeline = self.pipeline()
                review_entered, review_release = asyncio.Event(), asyncio.Event()

                async def review(text):
                    review_entered.set()
                    await review_release.wait()
                    return True

                pipeline.safety.approve = review
                state = pipeline._start_delay_notice()
                await asyncio.wait_for(review_entered.wait(), .5)
                if change == 'voice':
                    pipeline.transitions.current = False
                elif change == 'stage':
                    pipeline.stage.acquire('singing')
                else:
                    pipeline.output_clients.clear()
                review_release.set()
                await asyncio.wait_for(state['task'], .5)
                self.assertFalse(state['spoken'], 'a review result cannot validate stale playback state')
                self.assertTrue(pipeline.result_queue.empty())


class PreparedWaitVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_line_uses_and_caches_the_selected_character_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Pipeline(Config())
            pipeline.tts = FakeEngine(directory)
            state = SimpleNamespace(active_character='astra', characters=SimpleNamespace(profiles={
                name: SimpleNamespace(voice=SimpleNamespace(enabled=True)) for name in ('astra', 'other')}))
            helper = ModuleTransitions(pipeline, state)
            try:
                first = await helper.prepare_wait()
                second = await helper.prepare_wait()
                self.assertEqual(first, second)
                self.assertEqual(first['text'], WAIT_LINE)
                self.assertEqual(len(pipeline.tts.calls), 1)
                self.assertEqual(pipeline.tts.calls[0][2].mimo_style, 'Saved character voice')
                self.assertTrue(helper.matches_current(first))
                pipeline.tts.config.mimo_style = 'Different selected voice'
                self.assertFalse(helper.matches_current(first))
                replacement = await helper.prepare_wait()
                self.assertNotEqual(first['audio_url'], replacement['audio_url'])
                state.active_character = 'other'
                self.assertFalse(helper.matches_current(replacement))
                self.assertEqual(helper._tasks, set())
            finally:
                await helper.close()
                await pipeline.tts.close()


if __name__ == '__main__':
    unittest.main()
