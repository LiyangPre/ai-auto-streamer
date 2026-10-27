import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import lameenc
from mutagen.mp3 import MP3

from src.config import Config
import src.server as server


def fixture_mp3():
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96); encoder.set_in_sample_rate(24000); encoder.set_channels(1)
    return bytes(encoder.encode(b'\0' * 48000) + encoder.flush())


class SongPreparationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.preparation = self.root / 'prepared'
        self.preparation.mkdir()
        self.cfg = Config()
        self.cfg.llm.provider = 'mock'; self.cfg.tts.engine = 'none'; self.cfg.idle.enabled = False
        self.patches = [patch('src.server.Config.load', return_value=self.cfg),
                        patch('src.server.settings_path', return_value=self.root / 'studio-settings.json'),
                        patch('src.singing_routes.PREPARATION_ROOT', self.preparation),
                        patch('src.danmaku.sys.stdin.isatty', return_value=False)]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app, headers={'Origin': 'http://testserver', 'X-Studio-API': '1'})
        self.client.__enter__()
        self.studio = server.app.state.singing

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def prepare_complete_fixture(self, count=9):
        mix = self.preparation / 'mix.mp3'; mix.write_bytes(fixture_mp3())
        guide = self.preparation / 'vocals.mp3'; guide.write_bytes(fixture_mp3())
        reference = self.preparation / 'reference.wav'; reference.write_bytes(b'approved reference fixture')
        reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
        metadata = self.preparation / 'reference.json'
        voice = server.app.state.characters.profiles['astra'].voice
        voice.provider = 'mimo-tts'; voice.mimo_model = 'mimo-v2.5-tts-voicedesign'
        voice.mimo_style = 'Fixture current saved voice'
        metadata.write_text(json.dumps({'path': str(reference), 'approved': True, 'candidate_id': 'current-profile',
                                       'profile_id': 'astra', 'reference_sha256': reference_hash,
                                       'model': voice.mimo_model, 'style': voice.mimo_style}), encoding='utf-8')
        manifest = {'voice_candidate': 'current-profile', 'reference_sha256': reference_hash,
                    'reference_metadata': str(metadata), 'reference': str(reference), 'ready': count, 'total': count,
                    'tracks': [{'bvid': 'BV' + str(index).zfill(10), 'title': f'Track {index}',
                                'mix_mp3': str(mix), 'vocals_mp3': str(guide), 'ready': True,
                                'duration_seconds': MP3(mix).info.length, 'conversion': {'engine': 'Seed-VC', 'diffusion_steps': 50}}
                               for index in range(count)]}
        self.write_manifest(manifest)
        self.write_jobs(manifest)
        return manifest

    def write_jobs(self, manifest):
        jobs = {'voice_candidate': manifest['voice_candidate'], 'reference_sha256': manifest['reference_sha256'],
                'diffusion_steps': 50, 'tracks': [{'id': track['bvid'], 'bvid': track['bvid']} for track in manifest['tracks']]}
        (self.preparation / 'jobs.json').write_text(json.dumps(jobs), encoding='utf-8')

    def write_manifest(self, data):
        (self.preparation / 'prepared.json').write_text(json.dumps(data), encoding='utf-8')

    def test_prepared_import_is_local_fixed_path_and_refuses_partial_batch(self):
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={}, headers={'Origin': 'https://elsewhere.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={'path': 'elsewhere.json'}).status_code, 422)
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={}).status_code, 409)
        manifest = self.prepare_complete_fixture()
        manifest['tracks'][0]['ready'] = False
        self.write_manifest(manifest)
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={}).status_code, 409)
        self.assertEqual(self.studio.catalog(), [])
        self.assertIsNone(self.studio.playback)

    def test_complete_batch_imports_once_with_guides_backup_and_no_profile_switch_or_playback(self):
        self.prepare_complete_fixture()
        state = server.app.state
        state.characters.profiles['another'] = state.characters.profiles['astra'].model_copy(deep=True)
        state.active_character = 'another'
        response = self.client.post('/api/singing/import-prepared', json={})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(len(result['imported']), 9)
        self.assertFalse(result['playback_started'])
        self.assertNotIn('path', result['backup'])
        self.assertEqual(state.active_character, 'another')
        self.assertIsNone(self.studio.playback)
        self.assertFalse(state.pipeline.performance_active)
        self.assertEqual(len(self.studio.catalog()), 9)
        self.assertTrue(all(song['profile_id'] == 'astra' and song.get('guide_url') for song in self.studio.catalog()))
        second = self.client.post('/api/singing/import-prepared', json={})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['imported'], [])
        self.assertEqual(len(second.json()['skipped']), 9)
        self.assertIsNone(second.json()['backup'])

    def test_final_current_voice_guard_blocks_provider_style_model_and_reference_changes(self):
        manifest = self.prepare_complete_fixture()
        voice = server.app.state.characters.profiles['astra'].voice
        for field, changed in [('provider', 'edge-tts'), ('mimo_style', 'Another saved voice'), ('mimo_model', 'mimo-v2.5-tts')]:
            previous = getattr(voice, field)
            setattr(voice, field, changed)
            response = self.client.post('/api/singing/import-prepared', json={})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail']['code'], 'voice_changed')
            self.assertEqual(self.studio.catalog(), [])
            setattr(voice, field, previous)
        metadata = Path(manifest['reference_metadata'])
        document = json.loads(metadata.read_text(encoding='utf-8'))
        for field, changed in [('profile_id', 'another'), ('approved', False), ('reference_sha256', 'b' * 64)]:
            altered = {**document, field: changed}
            metadata.write_text(json.dumps(altered), encoding='utf-8')
            response = self.client.post('/api/singing/import-prepared', json={})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail']['code'], 'voice_changed')
            self.assertEqual(self.studio.catalog(), [])
        metadata.write_text(json.dumps(document), encoding='utf-8')
        Path(manifest['reference']).write_bytes(b'changed reference samples')
        response = self.client.post('/api/singing/import-prepared', json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'voice_changed')
        self.assertEqual(self.studio.catalog(), [])

    def test_four_track_batch_imports_and_rejects_partial_stale_or_lower_quality_files(self):
        stale = self.prepare_complete_fixture(9)
        active = self.prepare_complete_fixture(4)
        self.write_manifest(stale)
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={}).status_code, 409)
        partial = {**active, 'ready': 3}
        self.write_manifest(partial)
        self.assertEqual(self.client.post('/api/singing/import-prepared', json={}).status_code, 409)
        active['tracks'][0]['conversion']['diffusion_steps'] = 30
        self.write_manifest(active)
        response = self.client.post('/api/singing/import-prepared', json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'batch_changed')
        active['tracks'][0]['conversion']['diffusion_steps'] = 50
        self.write_manifest(active)
        response = self.client.post('/api/singing/import-prepared', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['imported']), 4)
        self.assertIsNone(self.studio.playback)

    def test_status_is_bounded_scalar_only_and_hides_private_paths(self):
        private = {'state': 'running', 'stage': 'converting', 'ready': 2, 'total': 9,
                   'current_title': 'Example song', 'message': 'Working at D:\\private\\secret.env',
                   'pid': 123, 'started': 1, 'updated': 2, 'estimated_seconds_remaining': 30,
                   'reference_path': 'D:\\private\\voice.wav', 'api_key': 'do-not-return', 'nested': {'anything': 'private'}}
        (self.preparation / 'pipeline-state.json').write_text(json.dumps(private), encoding='utf-8')
        response = self.client.get('/api/singing/preparation')
        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertEqual(status['ready'], 2)
        self.assertEqual(status['current_title'], 'Example song')
        self.assertNotIn('private', response.text)
        self.assertNotIn('api_key', status)
        self.assertTrue(all(type(value) in (str, int, float) for value in status.values()))
        self.assertEqual(self.client.get('/api/singing').json()['preparation'], status)
        self.assertEqual(self.client.get('/api/singing/preparation', headers={'Origin': 'https://elsewhere.example'}).status_code, 403)
        (self.preparation / 'pipeline-state.json').write_text('x' * 16385, encoding='utf-8')
        self.assertEqual(self.client.get('/api/singing/preparation').json()['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
