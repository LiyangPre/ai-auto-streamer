"""Temporary title bans cover chat, queued songs and operator playback."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pydantic import ValidationError

from src.config import Config
from src.danmaku import DanmakuMessage
from src.pipeline import Pipeline
from src.singing import SingingStudio, SongPlay
from src.song_requests import SongRequestPolicy, SongRequestQueue


def saved_song(index, title, profile='astra'):
    token = f'{index:032x}'
    return {'id': token, 'title': title, 'profile_id': profile, 'created': index,
            'duration': 30., 'url': '/api/singing/audio/' + token}


def state_for(pipeline):
    profiles = {name: SimpleNamespace(module_options={}, identity=SimpleNamespace(name=name, spoken_name=name))
                for name in ('astra', 'claude')}
    return SimpleNamespace(active_character='astra', characters=SimpleNamespace(profiles=profiles),
                           settings_lock=asyncio.Lock(),
                           modules=SimpleNamespace(presentation=lambda _: {'pose': 'idle', 'expression': 'singing'}))


class SongBanQueueTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline(Config())
        self.songs = [saved_song(1, '告白气球'), saved_song(2, '海屿你'), saved_song(3, '起风了'),
                      saved_song(4, '告白气球', 'claude')]
        self.studio = SimpleNamespace(pipeline=self.pipeline, catalog=lambda: self.songs, playback=None)
        self.state = state_for(self.pipeline)
        self.queue = SongRequestQueue(self.studio, self.state)

    def ban(self, titles=('告白气球', '海屿你')):
        self.state.characters.profiles[self.state.active_character].module_options['singing'] = {
            'blocked_titles': list(titles)}

    def enqueue(self, index):
        return self.queue.enqueue_id(f'{index:032x}', 1, '测试观众', source='controller')

    def test_policy_rejects_empty_unbounded_and_invalid_titles_and_deduplicates(self):
        for value in ([''], ['   '], ['《》'], ['x' * 81], [1], [str(i) for i in range(33)], '告白气球'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SongRequestPolicy(blocked_titles=value)
        policy = SongRequestPolicy(blocked_titles=['告白气球', '《告 白 气 球》', '海屿你', '海 屿 你'])
        self.assertEqual(policy.blocked_titles, ['告白气球', '海屿你'])

    def test_chat_and_controller_requests_reject_paused_titles(self):
        self.ban()
        for text in ('点歌《告 白 气 球》', '/song 海屿你'):
            with self.subTest(text=text):
                msg = DanmakuMessage(text, '测试观众', 1, time.time())
                self.assertTrue(self.queue.on_message(msg))
                self.assertEqual(self.queue.public()['last_result']['code'], 'banned')
        self.assertEqual(self.enqueue(1)['code'], 'banned')
        self.assertEqual(self.enqueue(2)['code'], 'banned')
        self.assertEqual(self.queue.items, [])
        self.assertTrue(self.enqueue(3)['accepted'])

    def test_future_import_remains_banned_and_unban_is_reversible(self):
        self.ban()
        self.songs[:] = [song for song in self.songs if song['title'] != '海屿你']
        self.assertTrue(self.queue.on_message(DanmakuMessage('点歌 海屿你', '观众', 2, time.time())))
        self.assertEqual(self.queue.public()['last_result']['code'], 'banned')
        self.songs.append(saved_song(2, '海屿你'))
        self.assertEqual(self.enqueue(2)['code'], 'banned')
        self.ban([])
        self.assertTrue(self.enqueue(2)['accepted'])

    def test_pruning_preserves_allowed_fifo_and_other_character_requests(self):
        for index in (1, 2, 3):
            self.assertTrue(self.enqueue(index)['accepted'])
        self.state.active_character = 'claude'
        self.assertTrue(self.enqueue(4)['accepted'])
        self.state.active_character = 'astra'
        self.ban()
        self.assertEqual(self.queue.prune_blocked(), 2)
        self.assertEqual([entry['song_id'] for entry in self.queue.items], [f'{3:032x}', f'{4:032x}'])
        self.assertEqual(self.queue.pop_next()['song_id'], f'{3:032x}')
        self.state.active_character = 'claude'
        self.assertFalse(self.queue.is_blocked('告白气球'))
        self.assertEqual(self.queue.public()['queue'][0]['song_id'], f'{4:032x}')

    def test_pop_skips_newly_paused_songs_even_without_explicit_prune(self):
        self.enqueue(1)
        self.enqueue(3)
        self.ban()
        self.assertEqual(self.queue.pop_next()['song_id'], f'{3:032x}')
        self.assertNotIn(f'{1:032x}', [item['song_id'] for item in self.queue.items])


class SongBanPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pipeline = Pipeline(Config())
        self.pipeline._emit = AsyncMock()
        self.state = state_for(self.pipeline)
        self.studio = SingingStudio(self.temp.name, self.pipeline, self.state)
        self.studio.prepare_transition_audio = AsyncMock(return_value={})
        self.studio.play = AsyncMock()
        for index, title in ((1, '告白气球'), (2, '海屿你'), (3, '起风了')):
            song = saved_song(index, title)
            path = Path(self.temp.name) / song['id']
            path.with_suffix('.json').write_text(json.dumps(song, ensure_ascii=False), encoding='utf-8')
            path.with_suffix('.mp3').write_bytes(b'existing song recording')

    async def asyncTearDown(self):
        await self.studio.close()

    def ban(self):
        self.state.characters.profiles['astra'].module_options['singing'] = {
            'blocked_titles': ['告白气球', '海屿你']}

    async def test_manual_play_is_blocked_before_transition_synthesis_and_preserves_audio(self):
        self.ban()
        audio = Path(self.temp.name) / (f'{1:032x}' + '.mp3')
        original = audio.read_bytes()
        with self.assertRaisesRegex(ValueError, '暂停'):
            await self.studio.activate(SongPlay(song_id=f'{1:032x}'))
        self.studio.prepare_transition_audio.assert_not_awaited()
        self.studio.play.assert_not_awaited()
        self.assertEqual(audio.read_bytes(), original)
        flags = {song['title']: song['blocked'] for song in self.studio.public()['songs']}
        self.assertEqual(flags, {'告白气球': True, '海屿你': True, '起风了': False})
        self.assertTrue(all('blocked' not in song for song in self.studio.catalog()))

    async def test_manual_play_rechecks_a_ban_changed_during_preparation(self):
        async def prepare():
            self.ban()
            return {}
        self.studio.prepare_transition_audio.side_effect = prepare
        with self.assertRaisesRegex(ValueError, '暂停'):
            await self.studio.activate(SongPlay(song_id=f'{1:032x}'))
        self.studio.play.assert_not_awaited()

    async def test_queued_ban_is_pruned_before_any_transition_request(self):
        self.assertTrue(self.studio.requests.enqueue_id(f'{1:032x}', 1, '观众')['accepted'])
        self.ban()
        self.pipeline.output_clients.add(object())
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(self.studio.requests.items, [])
        self.studio.prepare_transition_audio.assert_not_awaited()
        self.studio.play.assert_not_awaited()

    async def test_queue_rechecks_a_ban_changed_during_preparation(self):
        self.assertTrue(self.studio.requests.enqueue_id(f'{2:032x}', 1, '观众')['accepted'])
        self.pipeline.output_clients.add(object())
        async def prepare():
            self.ban()
            return {}
        self.studio.prepare_transition_audio.side_effect = prepare
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(self.studio.requests.items, [])
        self.studio.play.assert_not_awaited()
