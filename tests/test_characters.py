import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.characters import load_library, library_path, CharacterLibrary
from src.config import Config
from src.studio import StudioSettings, save_settings, load_saved
import src.server as server


class CharacterAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'studio-settings.json'
        self.config = Config()
        self.config.llm.provider = 'mock'
        self.config.tts.engine = 'none'
        self.config.idle.enabled = False
        self.config.idle.self_talk = False
        self.config.character.name = 'Astra'
        self.config.character.spoken_name = 'GPT'
        self.config.character.personality = '你是 Astra，也叫 GPT，温柔的银龙。'
        self.config.scene.background = 'car'
        self.patches = [patch('src.server.Config.load', return_value=self.config),
                        patch('src.server.settings_path', return_value=self.path),
                        patch('src.danmaku.sys.stdin.isatty', return_value=False)]
        for item in self.patches: item.start()
        self.client = TestClient(server.app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'}).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in self.patches: item.stop()
        self.directory.cleanup()

    def context(self):
        return self.client.get('/api/characters').json()

    def create(self, name='Mira'):
        response = self.client.post('/api/characters', json={
            'name': name, 'source_id': 'astra', 'revision': self.context()['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def activate(self, profile_id):
        return self.client.post('/api/characters/activate', json={
            'profile_id': profile_id, 'revision': self.context()['revision']})

    def test_profiles_keep_independent_identity_voice_actions_and_one_shared_stage(self):
        original = self.client.get('/api/studio').json()['settings']
        new_id = self.create()
        self.assertEqual(self.context()['active_id'], 'astra')
        self.assertEqual(self.activate(new_id).status_code, 200)
        snapshot = self.client.get('/api/studio').json()
        draft = snapshot['settings']
        self.assertEqual(draft['identity']['persona'], '你是 Mira，也叫 Mira，温柔的银龙。')
        draft['identity']['persona'] = '你是爱旅行的 Mira。'
        draft['voice'].update(provider='mimo-tts', mimo_voice='茉莉', mimo_style='温暖、自然。')
        draft['actions'].update(intensity=.25, warmup_lines=['想听旅行故事吗？'])
        draft['reply']['max_chars'] = 35
        draft['scene'].update(background='garden', music_volume=.13, companions=False)
        response = self.client.post('/api/studio', json=draft, headers={
            'X-Profile-ID':new_id, 'X-Profile-Revision':str(snapshot['profile_context']['revision'])})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.activate('astra').status_code, 200)
        astra = self.client.get('/api/studio').json()['settings']
        for section in ['identity','reply','voice','actions']:
            self.assertEqual(astra[section], original[section])
        self.assertEqual(astra['scene'], draft['scene'])
        self.assertEqual(self.activate(new_id).status_code, 200)
        self.assertEqual(self.client.get('/api/studio').json()['settings'], draft)
        self.assertEqual(server.pipeline.llm.character.name, 'Mira')
        self.assertEqual(self.config.idle.self_talk_prompts, ['想听旅行故事吗？'])
        # The legacy export contains the last active profile, but a restart still
        # selects Astra from the canonical library and retains the shared stage.
        self.client.__exit__(None, None, None)
        self.client = TestClient(server.app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'}).__enter__()
        self.assertEqual(self.context()['active_id'], 'astra')
        restarted = self.client.get('/api/studio').json()['settings']
        self.assertEqual(restarted['identity'], original['identity'])
        self.assertEqual(restarted['scene'], draft['scene'])
        self.assertEqual(len(self.context()['profiles']), 2)
        stored = json.loads(library_path(self.path).read_text(encoding='utf-8'))
        self.assertNotIn('scene', stored['profiles'][new_id])
        self.assertNotIn('api_key', json.dumps(stored))

    def test_stale_editor_and_invalid_names_cannot_overwrite_another_profile(self):
        old = self.client.get('/api/studio').json()
        new_id = self.create()
        self.activate(new_id)
        before = library_path(self.path).read_bytes()
        stale = self.client.post('/api/studio',json=old['settings'],headers={
            'X-Profile-ID':'astra','X-Profile-Revision':str(old['profile_context']['revision'])})
        self.assertEqual(stale.status_code, 409)
        wrong_id = self.client.post('/api/studio',json=old['settings'],headers={
            'X-Profile-ID':'astra','X-Profile-Revision':str(self.context()['revision'])})
        self.assertEqual(wrong_id.status_code, 409)
        self.assertEqual(self.client.post('/api/characters/activate',json={
            'profile_id':'astra','revision':0}).status_code, 409)
        for extra in [{'name':'  '},{'name':'Bad','api_key':'do-not-save'}, {'name':'Bad','artwork_id':'../../secret'}]:
            response=self.client.post('/api/characters',json={'revision':self.context()['revision'],**extra})
            self.assertEqual(response.status_code,422)
        self.assertEqual(library_path(self.path).read_bytes(),before)

    def test_switch_waits_for_reply_and_cancels_previous_voice_without_losing_viewers(self):
        new_id = self.create()
        async def lock(): await server.pipeline._reply_lock.acquire()
        self.client.portal.call(lock)
        self.assertEqual(self.activate(new_id).status_code,409)
        self.client.portal.call(server.pipeline._reply_lock.release)
        old_tts = AsyncMock()
        server.pipeline.tts = old_tts
        server.pipeline.memory.add(7,'Viewer','你好')
        server.pipeline.memory.remember_reply('我是 Astra')
        server.pipeline._warmup_bag=['旧的暖场台词']
        server.pipeline.memory._pending_gifts[7]='gift-marker'
        self.assertEqual(self.activate(new_id).status_code,200)
        old_tts.close.assert_awaited_once()
        self.assertEqual(server.pipeline._warmup_bag,[])
        self.assertEqual([event.content for event in server.pipeline.memory._global],['你好'])
        self.assertEqual(server.pipeline.memory.user_history(7)[0].content,'你好')
        self.assertEqual(server.pipeline.memory._pending_gifts[7],'gift-marker')
        server.pipeline.memory._pending_gifts.clear()

    def test_pending_reply_rejects_switch_and_file_failure_leaves_runtime_untouched(self):
        new_id = self.create()
        pending = AsyncMock(); pending.done = lambda: False
        server.pipeline._background_tasks.add(pending)
        self.assertEqual(self.activate(new_id).status_code,409)
        server.pipeline._background_tasks.clear()
        before = self.context()
        with patch('src.server.save_library',side_effect=OSError('disk unavailable')):
            self.assertEqual(self.activate(new_id).status_code,500)
            response=self.client.post('/api/characters',json={'name':'No save','revision':before['revision']})
            self.assertEqual(response.status_code,500)
        self.assertEqual(self.context(),before)
        self.assertEqual(self.config.character.name,'Astra')

    def test_initial_websocket_and_switch_broadcast_include_current_profile(self):
        new_id = self.create()
        with self.client.websocket_connect('ws://localhost/ws') as ws:
            initial=ws.receive_json()
            self.assertEqual(initial['profile_context']['active_id'],'astra')
            self.assertEqual(initial['settings']['identity']['name'],'Astra')
            self.activate(new_id)
            for _ in range(8):
                event=ws.receive_json()
                if event['type']=='settings_updated': break
            self.assertTrue(event['profile_changed'])
            self.assertEqual(event['profile_context']['active_id'],new_id)

    def test_warmup_validation_duplicate_names_and_missing_ids(self):
        self.create()
        self.assertEqual(self.client.post('/api/characters',json={
            'name':'Mira','revision':self.context()['revision']}).status_code,409)
        self.assertEqual(self.activate('missing').status_code,404)
        draft=self.client.get('/api/studio').json()['settings']
        for lines in [['x'*161],['   '],['line']*21]:
            draft['actions']['warmup_lines']=lines
            self.assertEqual(self.client.post('/api/studio',json=draft).status_code,422)

    def test_legacy_editor_preserves_active_profile_warmup_lines(self):
        original=self.client.get('/api/studio').json()['settings']
        draft=json.loads(json.dumps(original))
        del draft['actions']['warmup_lines']
        self.assertEqual(self.client.post('/api/studio',json=draft).status_code,200)
        saved=server.app.state.characters.profiles['astra']
        self.assertEqual(saved.actions.warmup_lines,original['actions']['warmup_lines'])


class CharacterMigrationTests(unittest.TestCase):
    def test_old_flat_settings_migrate_once_without_losing_customizations(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'studio-settings.json'
            config=Config()
            config.danmaku_filter.time_window=.02
            settings=StudioSettings.from_config(config)
            settings.identity.name='Astra大人'
            settings.voice.mimo_style='保持当前声音'
            settings.scene.background='car'
            save_settings(settings,path)
            fresh=Config();load_saved(fresh,path)
            library=load_library(fresh,library_path(path))
            self.assertEqual(library.profiles['astra'].settings(library.scene),settings)
            # Existing canonical data takes precedence over a subsequently changed export.
            fresh.character.name='Old export'
            self.assertEqual(load_library(fresh,library_path(path)),library)
            library_path(path).write_text('{broken',encoding='utf-8')
            with self.assertRaises(ValueError):load_library(fresh,library_path(path))
            self.assertEqual(library_path(path).read_text(encoding='utf-8'),'{broken')

    def test_profile_paths_are_isolated_for_alternative_configs(self):
        self.assertEqual(library_path('data/studio-settings.json'),Path('data/character-profiles.json'))
        self.assertNotEqual(library_path('data/studio-settings.qa.json'),library_path('data/studio-settings.json'))
