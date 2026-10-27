"""Regression coverage for asynchronous Stop/welcome/resume transitions."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.performance import PerformanceSession
from src.stage_activity import StageActivity


class PerformanceRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipeline = SimpleNamespace(stage=StageActivity(), _emit=AsyncMock(), _last_activity_time=0)
        self.session = PerformanceSession(self.pipeline, 'story')
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.session.close()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def wait_for_pause(self):
        async def paused():
            while not (self.session.playback or {}).get('paused'):
                await asyncio.sleep(0)
        await asyncio.wait_for(paused(), .5)

    async def test_stop_during_timer_cancellation_cannot_leave_a_welcome_waiting_forever(self):
        session = self.session
        await session.play({'vocal_delay': 0}, {'duration': 40, 'title': 'Story'})
        session.timer.cancel()
        await asyncio.gather(session.timer, return_exceptions=True)
        cancellation_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def timer_with_cleanup():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_started.set()
                await release_cleanup.wait()

        session.timer = self.spawn(timer_with_cleanup())
        await asyncio.sleep(0)
        stopped = self.spawn(session.stop())
        await asyncio.wait_for(cancellation_started.wait(), .5)
        welcomed = self.spawn(session.welcome({'text': 'Hello', 'duration': 1, 'audio_url': None}))
        await asyncio.sleep(0)
        release_cleanup.set()
        await asyncio.wait_for(asyncio.gather(stopped, welcomed), .5)
        self.assertIsNone(session.playback)
        self.assertIsNone(session.welcome_timer)
        self.assertTrue(session.welcome_done.is_set())
        self.assertIsNone(self.pipeline.stage.owner)

    async def test_stop_while_resume_event_is_sending_cancels_the_resumed_timer(self):
        resume_sending = asyncio.Event()
        release_resume = asyncio.Event()

        async def emit(**payload):
            if payload['type'] == 'performance_resume':
                resume_sending.set()
                await release_resume.wait()

        self.pipeline._emit = AsyncMock(side_effect=emit)
        session = self.session
        await session.play({'vocal_delay': 0}, {'duration': 40, 'title': 'Story'})
        welcomed = self.spawn(session.welcome({'text': 'Hello', 'duration': 1, 'audio_url': None}))
        await self.wait_for_pause()
        playback_id = session.playback['id']
        welcome_id = session.playback['welcome']['id']
        resumed = self.spawn(session.resume_welcome(playback_id, welcome_id))
        await asyncio.wait_for(resume_sending.wait(), .5)
        stopped = self.spawn(session.stop())
        await asyncio.sleep(0)
        release_resume.set()
        await asyncio.wait_for(asyncio.gather(resumed, stopped, welcomed), .5)
        self.assertIsNone(session.playback)
        self.assertIsNone(session.timer)
        self.assertIsNone(session.welcome_timer)
        self.assertTrue(session.welcome_done.is_set())
        self.assertIsNone(self.pipeline.stage.owner)

    async def test_duplicate_and_stale_resume_cannot_resume_or_stop_a_new_story(self):
        session = self.session
        await session.play({'vocal_delay': 0}, {'duration': 40, 'title': 'First'})
        welcomed = self.spawn(session.welcome({'text': 'Hello', 'duration': 1, 'audio_url': None}))
        await self.wait_for_pause()
        old_id = session.playback['id']
        old_welcome = session.playback['welcome']['id']
        await asyncio.gather(session.resume_welcome(old_id, old_welcome), session.resume_welcome(old_id, old_welcome))
        await asyncio.wait_for(welcomed, .5)
        resumes = [call for call in self.pipeline._emit.call_args_list if call.kwargs['type'] == 'performance_resume']
        self.assertEqual(len(resumes), 1)
        await session.play({'vocal_delay': 0}, {'duration': 40, 'title': 'Second'})
        new_id = session.playback['id']
        await session.resume_welcome(old_id, old_welcome)
        await session.complete(old_id)
        self.assertEqual(session.playback['id'], new_id)
        self.assertEqual(session.playback['song']['title'], 'Second')
        self.assertEqual(self.pipeline.stage.owner, 'story')
