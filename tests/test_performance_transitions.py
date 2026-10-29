"""Prepared spoken transitions must not drift songs or end unfinished stories."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.performance import PerformanceSession
from src.stage_activity import StageActivity


class PerformanceTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1800000000.
        self.clock = patch('src.performance.time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.pipeline = SimpleNamespace(stage=StageActivity(), _emit=AsyncMock(),
                                        _last_activity_time=0, _last_performance_end_time=0)
        self.session = PerformanceSession(self.pipeline, 'singing')
        self.sessions = [self.session]
        self.tasks = []
        self.clip = {'id': 'a' * 32, 'title': '海屿你', 'duration': 40,
                     'url': '/api/singing/audio/' + 'a' * 32}

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for session in self.sessions:
            await session.close()
        self.clock.stop()

    def pair(self, intro_seconds=1.2, label='一'):
        return {'intro': {'text': '那就一起听这首歌吧' + label,
                          'audio_url': '/audio/tts_100_abcdef01.mp3', 'duration': intro_seconds},
                'outro': {'text': '唱完啦，回来陪你们聊天' + label,
                          'audio_url': '/audio/tts_101_abcdef02.mp3', 'duration': 1.4}}

    def events(self, event_type):
        return [call.kwargs for call in self.pipeline._emit.call_args_list
                if call.kwargs.get('type') == event_type]

    async def test_no_intro_or_short_intro_keeps_existing_three_second_preload_lead(self):
        await self.session.play({'vocal_delay': 0}, self.clip)
        self.assertEqual(self.session.playback['starts_at'], self.now + 3)
        await self.session.stop()
        await self.session.play(self.pair(intro_seconds=1.2), self.clip)
        playback = self.session.playback
        self.assertAlmostEqual(playback['intro']['starts_at'], self.now + .15)
        self.assertEqual(playback['starts_at'], self.now + 3)
        self.assertEqual(playback['song']['duration'], self.clip['duration'])

    async def test_long_intro_delays_song_start_once_with_padding_but_preserves_song_length(self):
        await self.session.play(self.pair(intro_seconds=4.2), self.clip)
        playback = self.session.playback
        self.assertAlmostEqual(playback['intro']['starts_at'], self.now + .15)
        self.assertAlmostEqual(playback['starts_at'], self.now + 4.55)
        self.assertEqual(playback['song'], self.clip)
        starts = self.events('singing_start')
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['performance']['starts_at'], playback['starts_at'])

    async def test_natural_completion_emits_outro_once_even_for_duplicate_callbacks(self):
        pair = self.pair()
        await self.session.play(pair, self.clip)
        identity = self.session.playback['id']
        await asyncio.gather(self.session.complete(identity), self.session.complete(identity))
        await self.session.complete(identity)
        stops = self.events('singing_stop')
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['playback_id'], identity)
        self.assertEqual(stops[0]['return_to'], 'chat')
        self.assertEqual(stops[0]['transition']['text'], pair['outro']['text'])
        self.assertEqual(stops[0]['transition']['audio_url'], pair['outro']['audio_url'])
        self.assertIsNone(self.session.playback)
        self.assertIsNone(self.pipeline.stage.owner)
        self.assertTrue(self.pipeline.stage.idle.is_set())

    async def test_operator_stop_and_shutdown_never_claim_an_unfinished_song_is_complete(self):
        await self.session.play(self.pair(), self.clip)
        await self.session.stop()
        stops = self.events('singing_stop')
        self.assertEqual(len(stops), 1)
        self.assertFalse(stops[0].get('transition'))
        await self.session.play(self.pair(label='二'), self.clip)
        await self.session.close()
        stops = self.events('singing_stop')
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(not event.get('transition') for event in stops))

    async def test_replacement_and_stale_completion_never_play_old_outro(self):
        first = self.pair(label='旧')
        second = self.pair(label='新')
        await self.session.play(first, self.clip)
        old_id = self.session.playback['id']
        await self.session.play(second, self.clip)
        new_id = self.session.playback['id']
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(len(self.events('singing_stop')), 1)
        self.assertEqual(self.events('singing_stop')[0]['playback_id'], old_id)
        self.assertFalse(self.events('singing_stop')[0].get('transition'))
        await self.session.complete(old_id)
        self.assertEqual(self.session.playback['id'], new_id)
        self.assertEqual(len(self.events('singing_stop')), 1)
        await self.session.complete(new_id)
        transitions = [event['transition'] for event in self.events('singing_stop')
                       if event.get('transition')]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]['text'], second['outro']['text'])

    async def test_welcome_pause_resume_does_not_emit_outro_until_story_really_finishes(self):
        story = PerformanceSession(self.pipeline, 'story')
        self.sessions.append(story)
        await story.play(self.pair(), {**self.clip, 'title': '合租小故事'})
        identity = story.playback['id']
        welcome = asyncio.create_task(story.welcome({
            'text': '欢迎来到家里', 'audio_url': '/audio/tts_102_abcdef03.mp3', 'duration': 1}))
        self.tasks.append(welcome)

        async def paused():
            while not (story.playback or {}).get('paused'):
                await asyncio.sleep(0)

        await asyncio.wait_for(paused(), .5)
        self.assertFalse(self.events('story_stop'))
        await story.complete(identity)
        self.assertIsNotNone(story.playback)
        self.assertFalse(self.events('story_stop'))
        welcome_id = story.playback['welcome']['id']
        self.now += 2
        await story.resume_welcome(identity, welcome_id)
        await asyncio.wait_for(welcome, .5)
        self.assertFalse(story.playback.get('paused'))
        self.assertFalse(self.events('story_stop'))
        self.assertEqual(len(self.events('performance_resume')), 1)
        await story.complete(identity)
        stops = self.events('story_stop')
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['transition']['text'], self.pair()['outro']['text'])


if __name__ == '__main__':
    unittest.main()
