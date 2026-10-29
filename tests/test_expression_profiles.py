"""Character/module expression preferences; no model, TTS or Live2D rig required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.characters import ModulePresentation, load_library, save_library
from src.config import Config
import src.server as server


class ExpressionMigrationTests(unittest.TestCase):
    def test_old_module_entries_get_safe_defaults_without_rewriting_source(self):
        config = Config()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            library = load_library(config, path)
            raw = library.model_dump()
            raw['profiles']['astra']['modules'] = {'chat': {'pose': 'auto', 'expression': 'auto', 'motion_strength': .2}}
            path.write_text(json.dumps(raw), encoding='utf-8')
            before = path.read_bytes()
            loaded = load_library(config, path)
            look = loaded.profiles['astra'].modules['chat']
            self.assertTrue(look.expression_enabled)
            self.assertEqual(look.expression_strength, .55)
            self.assertEqual(look.motion_strength, .2)
            self.assertEqual(path.read_bytes(), before)
            save_library(loaded, path)
            self.assertEqual(load_library(config, path).profiles['astra'].modules['chat'], look)

    def test_strength_rejects_invalid_types_nonfinite_and_out_of_range_values(self):
        for value in [-.01, 1.01, float('nan'), float('inf'), -float('inf'), '0.5', True]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ModulePresentation(expression_strength=value)
        for value in [0, .55, 1]:
            self.assertEqual(ModulePresentation(expression_strength=value).expression_strength, value)
        for value in ['false', 0, None]:
            with self.subTest(enabled=value), self.assertRaises(ValidationError):
                ModulePresentation(expression_enabled=value)


class ExpressionProfileAPITests(unittest.TestCase):
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

    def module(self, module_id):
        return next(item for item in self.client.get('/api/modules').json()['modules'] if item['id'] == module_id)

    def save(self, module_id, values, context=None):
        context = context or self.client.get('/api/modules').json()
        return self.client.post('/api/modules/' + module_id + '/presentation', json={
            'profile_id': context['active_profile'], 'revision': context['revision'], 'presentation': values})

    def test_all_unsaved_modules_publish_expression_defaults(self):
        modules = self.client.get('/api/modules').json()['modules']
        self.assertEqual({module['id'] for module in modules}, {'chat', 'story', 'singing'})
        for module in modules:
            self.assertTrue(module['presentation']['expression_enabled'])
            self.assertEqual(module['presentation']['expression_strength'], .55)

    def test_controls_are_per_character_per_module_and_persist_without_changing_shared_stage_or_voice(self):
        before = self.client.get('/api/studio').json()['settings']
        looks = {'chat': {'expression_enabled': True, 'expression_strength': .75},
                 'story': {'expression_enabled': False, 'expression_strength': .2},
                 'singing': {'expression_enabled': True, 'expression_strength': .35}}
        for module_id, look in looks.items():
            result = self.save(module_id, look)
            self.assertEqual(result.status_code, 200, result.text)
            for key, value in look.items():
                self.assertEqual(self.module(module_id)['presentation'][key], value)
        original = server.app.state.characters.profiles['astra'].model_dump()
        context = self.client.get('/api/characters').json()
        created = self.client.post('/api/characters', json={'name': 'Mira', 'source_id': 'astra',
                                                          'revision': context['revision']})
        self.assertEqual(created.status_code, 200, created.text)
        other = created.json()['id']
        context = self.client.get('/api/characters').json()
        self.assertEqual(self.client.post('/api/characters/activate', json={
            'profile_id': other, 'revision': context['revision']}).status_code, 200)
        self.assertEqual(self.save('story', {'expression_enabled': True, 'expression_strength': .8}).status_code, 200)
        self.assertEqual(server.app.state.characters.profiles['astra'].model_dump(), original)
        after = self.client.get('/api/studio').json()['settings']
        self.assertEqual(after['scene'], before['scene'])
        self.assertEqual(after['voice'], before['voice'])
        self.assertEqual(server.pipeline.llm.call_count, 0)
        self.assertIsNone(server.pipeline.tts)
        loaded = load_library(self.config, server.app.state.character_path)
        self.assertFalse(loaded.profiles['astra'].modules['story'].expression_enabled)
        self.assertEqual(loaded.profiles['astra'].modules['story'].expression_strength, .2)
        self.assertTrue(loaded.profiles[other].modules['story'].expression_enabled)
        self.assertEqual(loaded.profiles[other].modules['story'].expression_strength, .8)

    def test_legacy_partial_save_preserves_existing_expression_preferences(self):
        self.assertEqual(self.save('story', {'expression_enabled': False, 'expression_strength': .3}).status_code, 200)
        response = self.save('story', {'pose': 'shy', 'expression': 'happy', 'motion_strength': .1})
        self.assertEqual(response.status_code, 200, response.text)
        look = self.module('story')['presentation']
        self.assertFalse(look['expression_enabled'])
        self.assertEqual(look['expression_strength'], .3)
        self.assertEqual((look['pose'], look['expression'], look['motion_strength']), ('shy', 'happy', .1))

    def test_invalid_or_stale_updates_do_not_modify_any_profile(self):
        stale = self.client.get('/api/modules').json()
        self.assertEqual(self.save('chat', {'expression_strength': .6}).status_code, 200)
        path = server.app.state.character_path
        before = path.read_bytes()
        self.assertEqual(self.save('chat', {'expression_strength': .1}, stale).status_code, 409)
        for values in [{'expression_strength': -.1}, {'expression_strength': 1.1},
                       {'expression_strength': '0.5'}, {'expression_enabled': 'false'},
                       {'expression_strength': .5, 'untrusted_parameter': 999}]:
            with self.subTest(values=values):
                self.assertEqual(self.save('chat', values).status_code, 422)
        self.assertEqual(path.read_bytes(), before)

    def test_raw_nonfinite_json_returns_safe_422_without_echoing_input(self):
        context = self.client.get('/api/modules').json()
        path = server.app.state.character_path
        before = path.read_bytes()
        for value in [float('nan'), float('inf'), -float('inf')]:
            payload = {'profile_id': context['active_profile'], 'revision': context['revision'],
                       'presentation': {'expression_strength': value}}
            with self.subTest(value=value):
                # Python's decoder accepts these non-standard literals. They
                # must not crash the validation-error JSON response serializer.
                response = self.client.post('/api/modules/chat/presentation', content=json.dumps(payload),
                                            headers={'Content-Type': 'application/json'})
                self.assertEqual(response.status_code, 422, response.text)
                details = response.json()['detail']
                self.assertTrue(details)
                for detail in details:
                    self.assertLessEqual(set(detail), {'loc', 'msg', 'type'})
                    self.assertNotIn('input', detail)
                    self.assertNotIn('ctx', detail)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
