"""Module scene choices must not overwrite the shared stage or voice."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.characters import ModulePresentation, load_library, save_library
from src.config import Config, SceneConfig
import src.server as server


class ModuleSceneMigrationTests(unittest.TestCase):
    def test_legacy_song_look_gets_music_room_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            library = load_library(Config(), path)
            raw = library.model_dump()
            raw['profiles']['astra']['modules'] = {
                'singing': {'pose': 'nod', 'expression': 'happy', 'motion_strength': .2},
                'chat': {'pose': 'auto'}, 'story': {'pose': 'think'}}
            path.write_text(json.dumps(raw), encoding='utf-8')
            before = path.read_bytes()
            loaded = load_library(Config(), path)
            looks = loaded.profiles['astra'].modules
            self.assertEqual(looks['singing'].scene_preset, 'singing-room')
            self.assertEqual(looks['singing'].pose, 'nod')
            self.assertEqual(looks['chat'].scene_preset, 'inherit')
            self.assertEqual(looks['story'].scene_preset, 'inherit')
            self.assertEqual(path.read_bytes(), before)
            save_library(loaded, path)
            self.assertEqual(load_library(Config(), path).profiles['astra'].modules, looks)

    def test_explicit_shared_stage_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            library = load_library(Config(), path)
            library.profiles['astra'].modules['singing'] = ModulePresentation(scene_preset='inherit')
            save_library(library, path)
            self.assertEqual(load_library(Config(), path).profiles['astra'].modules['singing'].scene_preset, 'inherit')

    def test_only_installed_scene_names_are_accepted(self):
        self.assertEqual(SceneConfig(background='singing-room').background, 'singing-room')
        for value in ['https://example.com/image.png', '../../private', 'custom', '', None, True]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ModulePresentation(scene_preset=value)


class ModuleSceneAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config()
        self.config.llm.provider = 'mock'
        self.config.tts.engine = 'none'
        self.config.idle.enabled = False
        self.config.idle.self_talk = False
        path = Path(self.temp.name) / 'studio-settings.json'
        for patcher in [patch('src.server.Config.load', return_value=self.config),
                        patch('src.server.settings_path', return_value=path),
                        patch('src.danmaku.sys.stdin.isatty', return_value=False)]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(server.app, base_url='http://localhost',
                                 headers={'X-Studio-API': '1', 'Origin': 'http://localhost'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def looks(self):
        return {item['id']: item['presentation'] for item in self.client.get('/api/modules').json()['modules']}

    def save(self, module_id, look):
        context = self.client.get('/api/modules').json()
        return self.client.post('/api/modules/' + module_id + '/presentation', json={
            'profile_id': context['active_profile'], 'revision': context['revision'], 'presentation': look})

    def test_only_singing_defaults_to_a_dedicated_scene(self):
        looks = self.looks()
        self.assertEqual(looks['singing']['scene_preset'], 'singing-room')
        self.assertEqual(looks['chat']['scene_preset'], 'inherit')
        self.assertEqual(looks['story']['scene_preset'], 'inherit')

    def test_scene_override_persists_without_changing_shared_stage_or_voice(self):
        before = self.client.get('/api/studio').json()['settings']
        response = self.save('singing', {'scene_preset': 'palace'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.looks()['singing']['scene_preset'], 'palace')
        # An older controller changing only the pose must retain the new field.
        response = self.save('singing', {'pose': 'nod'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.looks()['singing']['scene_preset'], 'palace')
        after = self.client.get('/api/studio').json()['settings']
        self.assertEqual(after['scene'], before['scene'])
        self.assertEqual(after['voice'], before['voice'])
        saved = load_library(self.config, server.app.state.character_path)
        self.assertEqual(saved.profiles['astra'].modules['singing'].scene_preset, 'palace')
        self.assertFalse(server.pipeline.performance_active)
        self.assertEqual(server.pipeline.llm.call_count, 0)

    def test_invalid_scene_does_not_modify_saved_profile(self):
        before = server.app.state.character_path.read_bytes()
        for value in ['uninstalled-scene', 'https://example.com/scene', None]:
            with self.subTest(value=value):
                response = self.save('singing', {'scene_preset': value})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(server.app.state.character_path.read_bytes(), before)

    def test_inherit_is_an_explicit_opt_out_and_other_modules_are_independent(self):
        response = self.save('singing', {'scene_preset': 'inherit'})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.save('story', {'scene_preset': 'garden'})
        self.assertEqual(response.status_code, 200, response.text)
        looks = self.looks()
        self.assertEqual(looks['singing']['scene_preset'], 'inherit')
        self.assertEqual(looks['story']['scene_preset'], 'garden')
        self.assertEqual(looks['chat']['scene_preset'], 'inherit')


if __name__ == '__main__':
    unittest.main()
