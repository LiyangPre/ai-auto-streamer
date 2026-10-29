import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.config import Config, TTSConfig
from src.pipeline import Pipeline
from src.studio import StudioSettings, settings_path
from src.tts import TTSEngine
from src.server import app
import src.server as server


class StudioAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'settings.json'
        self.config = Config()
        self.config.llm.provider = 'mock'
        self.config.tts.engine = 'none'
        self.config.idle.enabled = False
        self.config_patch = patch('src.server.Config.load', return_value=self.config)
        self.path_patch = patch('src.server.settings_path', return_value=self.path)
        self.config_patch.start(); self.path_patch.start()
        self.stdin_patch = patch('src.danmaku.sys.stdin.isatty', return_value=False)
        self.stdin_patch.start()
        self.client = TestClient(app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'})
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.config_patch.stop(); self.path_patch.stop()
        self.stdin_patch.stop()
        self.directory.cleanup()

    def test_identity_reply_voice_and_motion_apply_and_persist(self):
        payload = self.client.get('/api/studio').json()['settings']
        payload['identity'].update(name='露娜', spoken_name='露娜', persona='你是爱讲故事的银龙，性格温暖沉稳。')
        payload['reply'].update(style='成熟自然，直接回答。', max_chars=32, max_tokens=64, cooldown=0, collection_window=.15)
        payload['voice'].update(voice='zh-CN-XiaoxiaoNeural', rate=-2, pitch=0)
        payload['actions'].update(intensity=.4, automatic=False)
        response = self.client.post('/api/studio', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.config.character.name, '露娜')
        self.assertEqual(server.pipeline.llm.character.personality, payload['identity']['persona'])
        self.assertEqual(self.config.tts.rate, '-2%')
        self.assertEqual(server.pipeline.action_mapper.get_action('happy')['name'], 'idle')
        self.assertEqual(server.pipeline.action_mapper.get_action_by_name('wave')['name'], 'present')
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(stored, payload)
        self.assertNotIn('api_key', self.client.get('/api/studio').text)

    def test_invalid_values_cannot_write_credentials_or_arbitrary_actions(self):
        initial = self.client.get('/api/studio').json()['settings']
        for mutate in [
            lambda s: s.update(api_key='unit-test-secret'),
            lambda s: s['voice'].update(voice='arbitrary-voice'),
            lambda s: s['actions']['emotion_map'].update(happy='invalid'),
            lambda s: s['reply'].update(collection_window=0),
        ]:
            payload = json.loads(json.dumps(initial))
            mutate(payload)
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 422)
        self.assertFalse(self.path.exists())

    def test_voice_audition_uses_draft_without_saving(self):
        voice = self.client.get('/api/studio').json()['settings']['voice']
        voice['voice'] = 'zh-CN-YunxiNeural'
        with patch.object(app.state.preview_tts, 'synthesize', new=AsyncMock(return_value='/audio/test.mp3')):
            response = self.client.post('/api/voice-preview', json={'voice': voice, 'text': '你好，声音试听。'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['audio_url'], '/audio/test.mp3')
        self.assertEqual(self.config.tts.voice, 'zh-CN-XiaoxiaoNeural')
        self.assertFalse(self.path.exists())

    def test_mimo_direction_and_voice_persist_without_exposing_key(self):
        payload = self.client.get('/api/studio').json()['settings']
        payload['voice'].update(provider='mimo-tts', enabled=True, mimo_voice='冰糖', mimo_style='清冷、慵懒，略带气声。')
        with patch.dict('os.environ', {'MIMO_API_KEY': ''}):
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 422)
            self.assertEqual(self.client.post('/api/voice-preview', json={'voice': payload['voice'], 'text': '你好。'}).status_code, 422)
            self.assertFalse(self.path.exists())
        with patch.dict('os.environ', {'MIMO_API_KEY': 'private-test-mimo'}):
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 200)
            self.assertEqual(self.config.tts.mimo_style, payload['voice']['mimo_style'])
            with patch.object(app.state.preview_tts, 'synthesize', new=AsyncMock(return_value='/audio/test.mp3')):
                draft = dict(payload['voice'], mimo_voice='茉莉', mimo_style='轻快自然。')
                preview = self.client.post('/api/voice-preview', json={'voice': draft, 'text': '你好。'})
            self.assertEqual(preview.json()['voice'], '茉莉')
            self.assertEqual(app.state.preview_tts.config.mimo_style, '轻快自然。')
            self.assertEqual(self.config.tts.mimo_style, payload['voice']['mimo_style'])
            self.assertEqual(self.client.get('/api/config').json()['voice'], '冰糖')
            payload['voice']['enabled'] = False
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 200)
            current = self.client.get('/api/studio')
            self.assertTrue(current.json()['mimo_ready'])
            self.assertEqual(current.json()['settings']['voice']['provider'], 'mimo-tts')
            self.assertNotIn('private-test-mimo', current.text)
            self.assertNotIn('private-test-mimo', self.path.read_text(encoding='utf-8'))
            payload['voice']['mimo_voice'] = 'https://untrusted.invalid/voice.wav'
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 422)

    def test_glm_voice_uses_shared_key_and_preserves_provider_when_muted(self):
        with patch.dict('os.environ', {'GLM_API_KEY': ''}):
            payload = self.client.get('/api/studio').json()['settings']
            payload['voice'].update(provider='glm-tts', glm_voice='tongtong', enabled=True)
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 422)
            self.assertEqual(self.client.post('/api/voice-preview', json={'voice': payload['voice'], 'text': '你好。'}).status_code, 422)
            self.assertFalse(self.path.exists())
        with patch.dict('os.environ', {'GLM_API_KEY': 'private-test-glm'}):
            response = self.client.post('/api/studio', json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.config.tts.engine, 'glm-tts')
            with patch.object(app.state.preview_tts, 'synthesize', new=AsyncMock(return_value='/audio/test.mp3')):
                preview = self.client.post('/api/voice-preview', json={'voice': payload['voice'], 'text': '你好。'})
            self.assertEqual(preview.json()['voice'], 'tongtong')
            payload['voice']['enabled'] = False
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 200)
            current = self.client.get('/api/studio')
            self.assertEqual(current.json()['settings']['voice']['provider'], 'glm-tts')
            self.assertTrue(current.json()['glm_ready'])
            self.assertNotIn('private-test-glm', current.text)
            self.assertNotIn('private-test-glm', self.path.read_text(encoding='utf-8'))
            self.assertEqual(self.config.tts.engine, 'none')

    def test_cloud_voice_requires_key_without_changing_saved_settings(self):
        with patch.dict('os.environ', {'MINIMAX_API_KEY': ''}):
            payload = self.client.get('/api/studio').json()['settings']
            payload['voice'].update(provider='minimax', enabled=True)
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 422)
            self.assertEqual(self.client.post('/api/voice-preview', json={'voice': payload['voice'], 'text': '你好。'}).status_code, 422)
            self.assertFalse(self.path.exists())
            self.assertEqual(self.config.tts.engine, 'none')

    def test_cloud_voice_can_be_saved_muted_and_secret_never_leaves_server(self):
        payload = self.client.get('/api/studio').json()['settings']
        payload['voice'].update(provider='minimax', enabled=False, minimax_voice='female-chengshu')
        payload['reply']['read_chat'] = False
        with patch.dict('os.environ', {'MINIMAX_API_KEY': 'private-test-token'}):
            response = self.client.post('/api/studio', json=payload)
            self.assertEqual(response.status_code, 200)
            current = self.client.get('/api/studio')
            self.assertTrue(current.json()['minimax_ready'])
            self.assertEqual(current.json()['settings']['voice']['provider'], 'minimax')
            self.assertNotIn('private-test-token', current.text)
            self.assertNotIn('private-test-token', self.path.read_text(encoding='utf-8'))
            self.assertFalse(self.config.character.read_chat)

    def test_scene_settings_apply_broadcast_and_survive_restart(self):
        payload=self.client.get('/api/studio').json()['settings']
        scene={**payload['scene'],'background':'palace','effect':'sparkles','weather_amount':.6,'dim':.2,'companions':True,'bottom_blur':5,
               'character_x':-18,'character_y':4,'character_scale':1.25,
               'subtitle_seconds':7,'music_enabled':False,'music_volume':.12}
        payload['scene']=scene
        with self.client.websocket_connect('ws://localhost/ws') as socket:
            socket.receive_json()
            response=self.client.post('/api/studio',json=payload)
            self.assertEqual(response.status_code,200,response.text)
            event=socket.receive_json()
            self.assertEqual(event['type'],'settings_updated')
            self.assertEqual(event['settings']['scene'],scene)
        self.assertEqual(self.client.get('/api/config').json()['scene'],scene)
        from src.studio import load_saved
        restored=Config();load_saved(restored,self.path)
        self.assertEqual(restored.scene.model_dump(),scene)

    def test_quiet_room_warmup_controls_persist_and_validate(self):
        payload=self.client.get('/api/studio').json()['settings']
        payload['actions'].update(warmup_enabled=True,warmup_delay=30,warmup_interval=120)
        response=self.client.post('/api/studio',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.config.idle.self_talk)
        self.assertEqual((self.config.idle.self_talk_delay,self.config.idle.self_talk_interval),(30,120))
        from src.studio import load_saved
        restored=Config();load_saved(restored,self.path)
        self.assertTrue(restored.idle.self_talk)
        self.assertEqual(restored.idle.self_talk_interval,120)
        for field,value in [('warmup_delay',0),('warmup_delay',301),('warmup_interval',1),('warmup_interval',601)]:
            invalid=json.loads(json.dumps(payload));invalid['actions'][field]=value
            self.assertEqual(self.client.post('/api/studio',json=invalid).status_code,422)

    def test_scene_rejects_external_assets_invalid_modes_and_out_of_bounds_framing(self):
        initial=self.client.get('/api/studio').json()['settings']
        for patch in ({'background':'https://outside.example/image.png'},{'effect':'video'},
                      {'weather_amount':2},{'dim':-1},{'character_x':101},{'character_y':-100},
                      {'character_scale':0},{'character_scale':10},{'bottom_blur':-1},{'bottom_blur':13},
                      {'subtitle_seconds':0},{'subtitle_seconds':21},{'music_volume':-.1},{'music_volume':1},
                      {'url':'file:///test'}):
            payload=json.loads(json.dumps(initial));payload['scene'].update(patch)
            self.assertEqual(self.client.post('/api/studio',json=payload).status_code,422)
        self.assertFalse(self.path.exists())

    def test_pre_scene_saved_settings_load_with_scene_defaults(self):
        payload=self.client.get('/api/studio').json()['settings'];payload.pop('scene')
        payload['identity']['name']='Existing character'
        self.path.write_text(json.dumps(payload),encoding='utf-8')
        from src.studio import load_saved
        restored=Config();load_saved(restored,self.path)
        self.assertEqual(restored.character.name,'Existing character')
        self.assertEqual(restored.scene.background,'moonlit')
        self.assertEqual(restored.scene.character_scale,1)

    def test_car_scene_saves_without_changing_voice_or_other_defaults(self):
        from src.studio import load_saved
        payload = self.client.get('/api/studio').json()['settings']
        voice = payload['voice'].copy()
        payload['scene']['background'] = 'car'
        payload['scene']['effect'] = 'none'
        payload['scene']['car_motion'] = True
        payload['scene']['car_vibration'] = False
        response = self.client.post('/api/studio', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        restored = Config()
        load_saved(restored, self.path)
        self.assertEqual(restored.scene.background, 'car')
        self.assertTrue(restored.scene.car_motion)
        self.assertFalse(restored.scene.car_vibration)
        self.assertEqual(self.client.get('/api/config').json()['scene']['background'], 'car')
        self.assertEqual(self.client.get('/api/studio').json()['settings']['voice'], voice)
        for path in ('/static/car-scene.js', '/static/car-drive.js', '/static/scenes/car/countryside-loop-v1.png',
                     '/static/scenes/car/window-cutout-v1.png', '/static/scenes/car/astra-car-v1.png',
                     '/static/scenes/car/astra-car-clean-v1.png'):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_busy_reply_preserves_unsaved_edits(self):
        payload = self.client.get('/api/studio').json()['settings']
        self.client.portal.call(server.pipeline._reply_lock.acquire)
        try:
            self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 409)
        finally:
            self.client.portal.call(server.pipeline._reply_lock.release)

    def test_demo_catalog_rejects_unsupported_poses_and_migrates_saved_mapping(self):
        settings=self.client.get('/api/studio').json()['settings']
        settings['actions']['emotion_map']['happy']='heart'
        self.assertEqual(self.client.post('/api/studio',json=settings).status_code,200)
        self.assertEqual(server.pipeline.action_mapper.get_action('happy')['name'],'shy')
        self.assertEqual(self.client.get('/rig-preview').status_code,200)
        self.assertEqual(self.client.get('/api/config').json()['actions'],['idle','nod','think','shy','laugh','present'])
        for name in ('idle','nod','think','shy','laugh','present'):
            self.assertEqual(self.client.post('/api/test-event',json={'type':'action_test','action':name}).status_code,200)
        for name in ('not_a_pose','heart','cheer','double_wave','peek','stretch','curtsy'):
            self.assertEqual(self.client.post('/api/test-event',json={'type':'action_test','action':name}).status_code,422)

    def test_settings_are_loaded_again_on_restart(self):
        payload = self.client.get('/api/studio').json()['settings']
        payload['identity']['name'] = '新名字'
        self.assertEqual(self.client.post('/api/studio', json=payload).status_code, 200)
        from src.studio import load_saved
        restored = Config()
        load_saved(restored, self.path)
        self.assertEqual(restored.character.name, '新名字')
        self.assertEqual(restored.tts.engine, 'none')

    def test_invalid_saved_settings_do_not_prevent_startup(self):
        from src.studio import load_saved
        self.path.write_text('{broken json', encoding='utf-8')
        restored = Config()
        with self.assertLogs('src.studio', level='WARNING'):
            load_saved(restored, self.path)
        self.assertEqual(restored.character.name, Config().character.name)

    def test_director_line_bypasses_model_and_uses_action_mapping(self):
        with self.client.websocket_connect('ws://localhost/ws') as websocket:
            websocket.receive_json()
            response = self.client.post('/api/direct-reply', json={'text': '晚上好，欢迎来这里休息一下。', 'emotion': 'happy'})
            self.assertEqual(response.status_code, 200)
            event = websocket.receive_json()
            self.assertEqual(event['type'], 'response')
            self.assertEqual(event['text'], '晚上好，欢迎来这里休息一下。')
            self.assertEqual(event['action']['name'], 'laugh')
        self.assertEqual(server.pipeline.metrics['llm_calls'], 0)
        self.assertEqual(server.pipeline.metrics['templates'], 0)


class ProgressiveVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_audio_is_available_before_synthesis_finishes_and_shared(self):
        gate = asyncio.Event()
        calls = []
        class Communicate:
            def __init__(self, **kwargs):
                calls.append(kwargs)
            async def stream(self):
                yield {'type': 'audio', 'data': b'first-audio'}
                await gate.wait()
                yield {'type': 'audio', 'data': b'last-audio'}
        with tempfile.TemporaryDirectory() as directory, patch('src.tts.edge_tts.Communicate', Communicate):
            engine = TTSEngine(TTSConfig(), directory)
            first_events = []
            url = await engine.create_stream('Lunaria，晚上好。', on_first_audio=lambda: first_events.append(True))
            token = url.rsplit('/', 1)[1]
            first_client = engine.stream_bytes(token)
            second_client = engine.stream_bytes(token)
            self.assertEqual(await asyncio.wait_for(anext(first_client), .3), b'first-audio')
            self.assertFalse(engine.jobs[token].done)
            self.assertEqual(await anext(second_client), b'first-audio')
            self.assertEqual(first_events, [True])
            self.assertEqual(len(calls), 1)
            self.assertIn('露娜', calls[0]['text'])
            gate.set()
            await engine.jobs[token].task
            self.assertEqual(await anext(first_client), b'last-audio')
            cached = await engine.create_stream('Lunaria，晚上好。')
            self.assertTrue(cached.startswith('/audio/'))
            self.assertEqual(len(calls), 1)
            await first_client.aclose(); await second_client.aclose(); await engine.close()

    async def test_stream_failure_signals_error_and_removes_partial_file(self):
        class FailedVoice:
            def __init__(self, **kwargs): pass
            async def stream(self):
                raise RuntimeError('unavailable')
                yield
        with tempfile.TemporaryDirectory() as directory, patch('src.tts.edge_tts.Communicate', FailedVoice):
            engine = TTSEngine(TTSConfig(), directory)
            error = AsyncMock()
            url = await engine.create_stream('测试。', on_error=error)
            job = engine.jobs[url.rsplit('/', 1)[1]]
            await job.task
            self.assertTrue(job.done)
            self.assertTrue(job.error)
            self.assertFalse(job.path.exists())
            error.assert_awaited_once()
            await engine.close()

    async def test_natural_voice_has_no_emotion_pitch_jumps(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = TTSEngine(TTSConfig(expressive=False), directory)
            self.assertEqual(engine._voice_params('happy')['pitch'], '+0Hz')
            self.assertEqual(engine._voice_params('sad')['rate'], '+0%')
            engine.config.expressive = True
            self.assertNotEqual(engine._voice_params('happy')['pitch'], '+0Hz')

    async def test_short_comma_does_not_fragment_speech(self):
        pipeline = Pipeline(Config())
        sentence, pending = pipeline._take_sentence('晚上好，今天辛苦了')
        self.assertIsNone(sentence)
        self.assertEqual(pending, '晚上好，今天辛苦了')
        sentence, pending = pipeline._take_sentence('晚上好，今天辛苦了。来坐一会儿。')
        self.assertEqual(sentence, '晚上好，今天辛苦了。')
        self.assertEqual(pending, '来坐一会儿。')


if __name__ == '__main__':
    unittest.main()
