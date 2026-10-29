"""Local song ordering reuses saved audio and respects module ownership."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import lameenc

from src.config import Config
from src.characters import CharacterLibrary, CharacterProfile
from src.pipeline import Pipeline
from src.singing import SingingStudio, SongPlaybackRange
from src.studio import StudioSettings
import src.server as server


def fixture_mp3():
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(24000)
    encoder.set_channels(1)
    return bytes(encoder.encode(b'\0' * 48000) + encoder.flush())


class SongRequestAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cfg = Config()
        cfg.llm.provider = 'mock'
        cfg.tts.engine = 'none'
        cfg.platform.type = 'text'
        cfg.idle.enabled = False
        for patcher in (
            patch('src.server.Config.load', return_value=cfg),
            patch('src.server.settings_path', return_value=self.root / 'studio-settings.json'),
            patch('src.danmaku.sys.stdin.isatty', return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        provider_patch = patch('src.singing.mimo_audio_chunks')
        self.provider = provider_patch.start()
        self.addCleanup(provider_patch.stop)
        self.client = TestClient(server.app, base_url='http://localhost',
                                 headers={'X-Studio-API': '1', 'Origin': 'http://localhost'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.state = server.app.state
        self.studio = self.state.singing
        self.studio.root.mkdir(parents=True, exist_ok=True)
        self.songs = [self.add_song('a' * 32, '起风了'),
                      self.add_song('b' * 32, '小幸运'),
                      self.add_song('c' * 32, '另一个角色的歌', 'other')]
        current = self.context()
        policy = {**current['requests']['policy'], 'auto_play': False}
        response = self.client.post('/api/singing/request-policy', json={
            'profile_id': 'astra', 'revision': current['revision'], 'policy': policy})
        self.assertEqual(response.status_code, 200, response.text)

    def add_song(self, token, title, profile_id='astra'):
        audio = fixture_mp3()
        song = {'id': token, 'title': title, 'profile_id': profile_id, 'duration': 40,
                'created': 1, 'voice': 'Fixture voice', 'bytes': len(audio),
                'source': 'converted', 'generation_ms': 0,
                'url': '/api/singing/audio/' + token}
        (self.studio.root / (token + '.mp3')).write_bytes(audio)
        (self.studio.root / (token + '.json')).write_text(
            json.dumps(song, ensure_ascii=False), encoding='utf-8')
        return song

    def context(self):
        response = self.client.get('/api/singing')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def request(self, token):
        return self.client.post('/api/singing/requests', json={'song_id': token})

    def test_all_request_controls_reject_cross_origin_and_missing_header(self):
        current = self.context()
        endpoints = [('/requests', {'song_id': self.songs[0]['id']}),
                     ('/requests/remove', {'request_id': 'd' * 32}),
                     ('/requests/clear', {}),
                     ('/request-policy', {'profile_id': 'astra', 'revision': current['revision'],
                                          'policy': current['requests']['policy']})]
        for path, body in endpoints:
            with self.subTest(path=path):
                for headers in ({'Origin': 'https://elsewhere.example'}, {'X-Studio-API': ''}):
                    response = self.client.post('/api/singing' + path, json=body, headers=headers)
                    self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.context()['requests']['queue'], [])

    def test_only_stage_output_websocket_counts_for_automatic_playback(self):
        def handshake(socket):
            self.assertEqual(socket.receive_json()['type'], 'status')
            socket.send_json({'type': 'ping'})
            for _ in range(10):
                if socket.receive_json()['type'] == 'pong':
                    return
            self.fail('WebSocket ping did not reach the registered receive loop')

        with self.client.websocket_connect('ws://localhost/ws', headers={'Origin': 'http://localhost'}) as controller:
            handshake(controller)
            self.assertTrue(self.state.pipeline.ws_clients)
            self.assertFalse(self.state.pipeline.output_clients)
            with self.client.websocket_connect('ws://localhost/ws?role=preview', headers={'Origin': 'http://localhost'}) as preview:
                handshake(preview)
                self.assertFalse(self.state.pipeline.output_clients)
                with self.client.websocket_connect('ws://localhost/ws?role=output', headers={'Origin': 'http://localhost'}) as output:
                    handshake(output)
                    self.assertEqual(len(self.state.pipeline.output_clients), 1)
                self.context()  # Let the disconnect/finally finish on the server loop.
                self.assertFalse(self.state.pipeline.output_clients)
        self.context()
        self.assertFalse(self.state.pipeline.ws_clients)
        self.assertFalse(self.state.pipeline.output_clients)

    def test_viewer_request_reaches_queue_without_generation_or_stage_change(self):
        response = self.client.post('/api/danmaku', json={
            'content': '点歌 起风了', 'user_name': '小观众', 'user_id': 1001})
        self.assertEqual(response.status_code, 200, response.text)
        queue = self.context()['requests']['queue']
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['song_id'], self.songs[0]['id'])
        self.assertEqual(queue[0]['title'], '起风了')
        self.assertEqual(queue[0]['requester'], '小观众')
        self.assertNotIn('user_id', queue[0])
        self.assertTrue(self.state.pipeline.danmaku_queue.empty())
        self.assertIsNone(self.studio.playback)
        self.assertEqual(self.client.get('/api/status').json()['active_module'], 'chat')
        self.provider.assert_not_called()

    def test_local_queue_cannot_order_missing_or_other_characters_audio(self):
        for token in ('f' * 32, self.songs[2]['id']):
            with self.subTest(token=token):
                response = self.request(token)
                self.assertIn(response.status_code, (404, 409, 422), response.text)
        self.assertEqual(self.context()['requests']['queue'], [])
        response = self.client.post('/api/danmaku', json={
            'content': '点歌 尚未准备好的歌', 'user_name': '观众', 'user_id': 1002})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.context()['requests']['queue'], [])
        self.assertTrue(self.state.pipeline.danmaku_queue.empty())
        self.provider.assert_not_called()

    def test_operator_can_build_remove_and_clear_playlist_without_user_cooldown(self):
        for song in self.songs[:2]:
            response = self.request(song['id'])
            self.assertEqual(response.status_code, 200, response.text)
        queue = self.context()['requests']['queue']
        self.assertEqual([item['song_id'] for item in queue], [song['id'] for song in self.songs[:2]])
        duplicate = self.request(self.songs[0]['id'])
        self.assertIn(duplicate.status_code, (409, 429), duplicate.text)
        removed = self.client.post('/api/singing/requests/remove', json={'request_id': queue[0]['id']})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual([item['song_id'] for item in self.context()['requests']['queue']], [self.songs[1]['id']])
        cleared = self.client.post('/api/singing/requests/clear', json={})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(self.context()['requests']['queue'], [])
        self.provider.assert_not_called()

    def test_request_policy_is_revision_checked_persisted_and_character_owned(self):
        other = self.state.characters.profiles['astra'].model_copy(deep=True)
        other.module_options['singing'] = {'chat_requests': False, 'auto_play': False}
        self.state.characters.profiles['other'] = other
        current = self.context()
        policy = {**current['requests']['policy'], 'chat_requests': False,
                  'intermission_seconds': 25}
        payload = {'profile_id': 'astra', 'revision': current['revision'], 'policy': policy}
        response = self.client.post('/api/singing/request-policy', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.context()
        self.assertEqual(saved['revision'], current['revision'] + 1)
        self.assertFalse(saved['requests']['policy']['chat_requests'])
        self.assertEqual(saved['requests']['policy']['intermission_seconds'], 25)
        disk = json.loads(self.state.character_path.read_text(encoding='utf-8'))
        self.assertEqual(disk['profiles']['astra']['module_options']['singing'], policy)
        self.assertEqual(disk['profiles']['other']['module_options']['singing'],
                         {'chat_requests': False, 'auto_play': False})
        stale = self.client.post('/api/singing/request-policy', json=payload)
        self.assertEqual(stale.status_code, 409, stale.text)

    def test_policy_validation_rejects_unbounded_and_nonfinite_values_atomically(self):
        current = self.context()
        for changes in ({'max_queue': 10000}, {'user_cooldown': -1},
                        {'intermission_seconds': 0}, {'auto_play': 'false'}):
            with self.subTest(changes=changes):
                body = {'profile_id': 'astra', 'revision': current['revision'],
                        'policy': {**current['requests']['policy'], **changes}}
                response = self.client.post('/api/singing/request-policy', json=body)
                self.assertEqual(response.status_code, 422, response.text)
        nonfinite = {'profile_id': 'astra', 'revision': current['revision'],
                     'policy': {**current['requests']['policy'], 'intermission_seconds': float('nan')}}
        response = self.client.post('/api/singing/request-policy',
                                    content=json.dumps(nonfinite), headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.context()['revision'], current['revision'])
        self.assertEqual(self.context()['requests']['policy'], current['requests']['policy'])

    def test_saved_song_bans_reject_requests_and_manual_play_before_transition_preparation(self):
        paused = [self.add_song('d' * 32, '告白气球'), self.add_song('e' * 32, '海屿你')]
        current = self.context()
        policy = {**current['requests']['policy'], 'blocked_titles': ['告白气球', '海屿你']}
        saved = self.client.post('/api/singing/request-policy', json={
            'profile_id': 'astra', 'revision': current['revision'], 'policy': policy})
        self.assertEqual(saved.status_code, 200, saved.text)
        with patch.object(self.studio, 'prepare_transition_audio', new=AsyncMock(return_value={})) as prepare, \
                patch.object(self.studio, 'play', new=AsyncMock()) as stage_play:
            for song in paused:
                with self.subTest(title=song['title']):
                    ordered = self.request(song['id'])
                    self.assertEqual(ordered.status_code, 403, ordered.text)
                    played = self.client.post('/api/singing/play', json={'song_id': song['id']})
                    self.assertEqual(played.status_code, 403, played.text)
            response = self.client.post('/api/danmaku', json={
                'content': '点歌《告白气球》', 'user_name': '点歌观众', 'user_id': 1010})
            self.assertEqual(response.status_code, 200, response.text)
            context = self.context()
            self.assertEqual(context['requests']['last_result']['code'], 'banned')
            self.assertEqual(context['requests']['queue'], [])
            flags = {song['title']: song['blocked'] for song in context['songs']}
            self.assertTrue(flags['告白气球'])
            self.assertTrue(flags['海屿你'])
            self.assertFalse(flags['起风了'])
            prepare.assert_not_awaited()
            stage_play.assert_not_awaited()
            self.provider.assert_not_called()

            unbanned = self.client.post('/api/singing/request-policy', json={
                'profile_id': 'astra', 'revision': context['revision'],
                'policy': {**context['requests']['policy'], 'blocked_titles': []}})
            self.assertEqual(unbanned.status_code, 200, unbanned.text)
            self.assertEqual(self.request(paused[0]['id']).status_code, 200)
            played = self.client.post('/api/singing/play', json={'song_id': paused[0]['id']})
            self.assertEqual(played.status_code, 200, played.text)
            prepare.assert_awaited_once()
            stage_play.assert_awaited_once()
        for song in paused:
            self.assertTrue((self.studio.root / (song['id'] + '.mp3')).is_file())

    def test_banning_current_song_stops_to_chat_and_prunes_only_paused_waiting_songs(self):
        current_song = self.add_song('d' * 32, '告白气球')
        waiting_song = self.add_song('e' * 32, '海屿你')
        played = self.client.post('/api/singing/play', json={'song_id': current_song['id']})
        self.assertEqual(played.status_code, 200, played.text)
        self.assertEqual(self.client.get('/api/status').json()['active_module'], 'singing')
        self.assertEqual(self.request(waiting_song['id']).status_code, 200)
        self.assertEqual(self.request(self.songs[1]['id']).status_code, 200)
        before = self.context()
        policy = {**before['requests']['policy'], 'blocked_titles': ['告白气球', '海屿你']}
        saved = self.client.post('/api/singing/request-policy', json={
            'profile_id': 'astra', 'revision': before['revision'], 'policy': policy})
        self.assertEqual(saved.status_code, 200, saved.text)
        context = self.context()
        self.assertIsNone(context['playback'])
        self.assertEqual(self.client.get('/api/status').json()['active_module'], 'chat')
        self.assertEqual([item['song_id'] for item in context['requests']['queue']], [self.songs[1]['id']])
        self.assertEqual(context['requests']['policy']['blocked_titles'], ['告白气球', '海屿你'])
        self.provider.assert_not_called()

    def test_requests_during_song_do_not_interrupt_and_completion_returns_to_chat(self):
        response = self.client.post('/api/singing/play', json={'song_id': self.songs[0]['id']})
        self.assertEqual(response.status_code, 200, response.text)
        playback_id = response.json()['playback']['id']
        response = self.client.post('/api/danmaku', json={
            'content': '/song 小幸运', 'user_name': '另一位观众', 'user_id': 1003})
        self.assertEqual(response.status_code, 200, response.text)
        current = self.context()
        self.assertEqual(current['playback']['id'], playback_id)
        self.assertEqual([entry['song_id'] for entry in current['requests']['queue']], [self.songs[1]['id']])
        completed = self.client.post('/api/modules/singing/complete', json={'playback_id': playback_id})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()['active_module'], 'chat')
        self.assertIsNone(self.context()['playback'])
        self.assertEqual(len(self.context()['requests']['queue']), 1)
        self.provider.assert_not_called()


class SongRequestPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        cfg = Config()
        cfg.llm.provider = 'mock'
        cfg.tts.engine = 'none'
        self.pipeline = Pipeline(cfg)
        self.pipeline._emit = AsyncMock()
        profile = CharacterProfile.from_settings(StudioSettings.from_config(cfg))
        library = CharacterLibrary(profiles={'astra': profile}, scene=cfg.scene)
        self.state = SimpleNamespace(active_character='astra', characters=library,
                                     settings_lock=asyncio.Lock(), runtime_config=cfg,
                                     modules=SimpleNamespace(presentation=lambda module_id: {
                                         'pose': 'idle', 'expression': 'singing', 'motion_strength': .35}))
        self.studio = SingingStudio(self.temp.name, self.pipeline, self.state)
        self.songs = []
        audio = fixture_mp3()
        for token, title in [('a' * 32, '起风了'), ('b' * 32, '小幸运')]:
            item = {'id': token, 'title': title, 'profile_id': 'astra', 'duration': 40,
                    'created': 1, 'bytes': len(audio), 'voice': 'Fixture voice',
                    'source': 'converted', 'generation_ms': 0,
                    'url': '/api/singing/audio/' + token}
            root = Path(self.temp.name)
            (root / (token + '.json')).write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            (root / (token + '.mp3')).write_bytes(audio)
            self.songs.append(item)

    async def asyncTearDown(self):
        await self.studio.close()
        self.temp.cleanup()

    def enqueue(self, index=0):
        result = self.studio.requests.enqueue_id(self.songs[index]['id'], 1, 'Console', source='controller')
        self.assertTrue(result['accepted'], result)

    async def test_audience_order_uses_latest_saved_song_range(self):
        self.enqueue()
        self.studio.set_playback_range(self.songs[0]['id'],SongPlaybackRange(start_seconds=10,end_seconds=25),'astra')
        self.pipeline.output_clients.add(object())
        self.assertTrue(await self.studio._advance_requests())
        self.assertEqual(self.studio.playback['start_offset'],10)
        self.assertEqual(self.studio.playback['play_duration'],15)
        self.assertEqual(self.studio.playback['song']['duration'],40)
        self.assertFalse(self.studio.requests.public()['queue'])

    async def test_auto_play_waits_for_output_story_reply_and_actual_voice_completion(self):
        self.enqueue()
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(len(self.studio.requests.public()['queue']), 1)
        self.pipeline.ws_clients.add(object())
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(len(self.studio.requests.public()['queue']), 1)
        self.pipeline.output_clients.add(object())
        self.pipeline.stage.acquire('story')
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(self.pipeline.stage.owner, 'story')
        self.pipeline.stage.release('story')
        await self.pipeline._reply_lock.acquire()
        self.assertFalse(await self.studio._advance_requests())
        self.pipeline._reply_lock.release()
        self.pipeline.tts = SimpleNamespace(jobs={'pending': SimpleNamespace(done=False)})
        self.assertFalse(await self.studio._advance_requests())
        self.pipeline.tts.jobs['pending'].done = True
        with patch.object(self.pipeline, 'speech_pending', return_value=True):
            self.assertFalse(await self.studio._advance_requests())
        self.pipeline.tts = None
        self.assertEqual(len(self.studio.requests.public()['queue']), 1)
        self.assertTrue(await self.studio._advance_requests())
        self.assertEqual(self.pipeline.stage.owner, 'singing')
        self.assertEqual(self.studio.playback['song']['id'], self.songs[0]['id'])
        self.assertAlmostEqual(self.studio.playback['starts_at'] - time.time(), 3, delta=.25)

    async def test_natural_completion_returns_chat_then_observes_intermission(self):
        self.pipeline.output_clients.add(object())
        self.enqueue(0)
        self.enqueue(1)
        self.assertTrue(await self.studio._advance_requests())
        first = self.studio.playback['id']
        await self.studio.complete(first)
        self.assertIsNone(self.pipeline.stage.owner)
        self.assertIsNone(self.studio.playback)
        self.assertFalse(await self.studio._advance_requests())
        self.assertEqual(len(self.studio.requests.public()['queue']), 1)
        self.pipeline._last_performance_end_time -= 20
        self.assertTrue(await self.studio._advance_requests())
        self.assertEqual(self.studio.playback['song']['id'], self.songs[1]['id'])

    async def test_character_change_does_not_play_another_characters_queue(self):
        self.pipeline.output_clients.add(object())
        self.enqueue()
        self.state.characters.profiles['other'] = self.state.characters.profiles['astra'].model_copy(deep=True)
        self.state.active_character = 'other'
        self.assertFalse(await self.studio._advance_requests())
        self.assertIsNone(self.studio.playback)
        self.state.active_character = 'astra'
        self.assertTrue(await self.studio._advance_requests())
        self.assertEqual(self.studio.playback['song']['profile_id'], 'astra')



if __name__ == '__main__':
    unittest.main()
