"""Protected policy routes, using real character persistence and intent state."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.audience_interaction import AudienceInteraction
from src.characters import load_library
from src.config import Config
from src.interaction_routes import router


class InteractionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = FastAPI()
        self.app.include_router(router)
        self.state = self.app.state
        self.state.character_path = Path(self.temp.name) / 'profiles.json'
        self.state.characters = load_library(Config(), self.state.character_path)
        self.original_file = self.state.character_path.read_bytes()
        self.state.characters.profiles['claude'] = self.state.characters.profiles['astra'].model_copy(deep=True)
        self.state.characters.profiles['astra'].module_options['story'] = {'auto_idle': False}
        self.state.active_character = 'astra'
        self.state.settings_lock = asyncio.Lock()
        self.events = []

        async def emit(**event):
            self.events.append(event)

        self.state.pipeline = SimpleNamespace(_emit=emit)
        self.state.modules = SimpleNamespace(public=lambda: {'active_profile': self.state.active_character})
        self.state.interactions = AudienceInteraction(self.state)
        self.client = TestClient(self.app, base_url='http://localhost')
        self.headers = {'X-Studio-API': '1', 'Origin': 'http://localhost'}

    def payload(self, **policy):
        view = self.state.interactions.view()
        return {key: view[key] for key in ('profile_id', 'revision')} | {'policy': view['policy'] | policy}

    def post(self, payload=None):
        return self.client.post('/api/interactions', json=payload or self.payload(), headers=self.headers)

    def test_private_reads_and_writes_require_local_boundary(self):
        for method in ('get', 'post'):
            request = getattr(self.client, method)
            args = {'json': {}} if method == 'post' else {}
            for headers in ({}, {'X-Studio-API': '1', 'Origin': 'https://foreign.example'},
                            {'X-Studio-API': '1', 'Host': 'foreign.example'},
                            {'X-Studio-API': '1', 'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(method=method, headers=headers):
                    response = request('/api/interactions', headers=headers, **args)
                    self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.state.character_path.read_bytes(), self.original_file)
        self.assertEqual(self.events, [])

    def test_get_returns_profile_policy_and_only_public_pool_state(self):
        self.state.interactions.pool.record('astra', 'private-user', 'private-message',
                                            {'intent': 'story', 'confidence': .95})
        response = self.client.get('/api/interactions', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        view = response.json()
        self.assertEqual(set(view), {'profile_id', 'revision', 'policy', 'state'})
        self.assertEqual(view['profile_id'], 'astra')
        self.assertEqual(view['policy']['threshold'], 6)
        self.assertEqual(view['state']['counts']['story'], 1)
        self.assertNotIn('private-user', response.text)
        self.assertNotIn('private-message', response.text)

    def test_save_is_character_owned_persistent_and_emits_revision(self):
        old_voice = self.state.characters.profiles['astra'].voice.model_dump()
        result = self.post(self.payload(threshold=8, narrate_scene_changes=False))
        self.assertEqual(result.status_code, 200, result.text)
        view = result.json()
        self.assertEqual(view['revision'], 1)
        self.assertEqual(view['policy']['threshold'], 8)
        self.assertFalse(view['policy']['narrate_scene_changes'])
        saved = json.loads(self.state.character_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['profiles']['astra']['module_options']['interaction']['threshold'], 8)
        self.assertNotIn('interaction', saved['profiles']['claude']['module_options'])
        self.assertEqual(saved['profiles']['astra']['module_options']['story'], {'auto_idle': False})
        self.assertEqual(self.state.characters.profiles['astra'].voice.model_dump(), old_voice)
        self.assertEqual([event['type'] for event in self.events],
                         ['module_settings_updated', 'audience_interaction_update'])
        self.assertEqual(self.events[0]['profile_context']['revision'], 1)

    def test_revision_and_active_profile_guards_reject_stale_save(self):
        stale = self.payload()
        self.assertEqual(self.post(stale).status_code, 200)
        before = self.state.character_path.read_bytes()
        self.assertEqual(self.post(stale).status_code, 409)
        wrong = self.payload()
        wrong['profile_id'] = 'claude'
        self.assertEqual(self.post(wrong).status_code, 409)
        self.assertEqual(self.state.character_path.read_bytes(), before)
        self.assertEqual(self.state.characters.revision, 1)

    def test_invalid_payloads_fail_before_writing_or_publishing(self):
        examples = [self.payload(threshold=1), self.payload(enabled='false'),
                    self.payload(narrate_scene_changes='true'), self.payload(unexpected=True),
                    self.payload() | {'revision': '0'}, self.payload() | {'profile_id': '../astra'},
                    self.payload() | {'other': 'value'}]
        for payload in examples:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 422)
        self.assertEqual(self.state.character_path.read_bytes(), self.original_file)
        self.assertEqual(self.state.characters.revision, 0)
        self.assertEqual(self.events, [])

    def test_disk_failure_keeps_memory_votes_and_revision_and_hides_path(self):
        self.state.interactions.pool.record('astra', 1, 'vote', {'intent': 'story', 'confidence': .95})
        old = self.state.characters
        with patch('src.interaction_routes.save_library', side_effect=OSError('private/path/api-token')):
            response = self.post(self.payload(enabled=False))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private', response.text)
        self.assertIs(self.state.characters, old)
        self.assertEqual(self.state.interactions.view()['state']['counts']['story'], 1)
        self.assertEqual(self.events, [])

    def test_disabling_immediately_cancels_votes_and_song_selection(self):
        pool = self.state.interactions.pool
        pool.record('astra', 1, 'vote', {'intent': 'story', 'confidence': .95})
        pool.begin_song_selection('astra')
        response = self.post(self.payload(enabled=False))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state']['counts'], {'singing': 0, 'story': 0})
        self.assertFalse(response.json()['state']['awaiting_song'])

    def test_unavailable_service_has_a_readable_response(self):
        self.state.interactions = None
        self.assertEqual(self.client.get('/api/interactions', headers=self.headers).status_code, 503)
        response = self.post({'profile_id': 'astra', 'revision': 0, 'policy': {}})
        self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()
