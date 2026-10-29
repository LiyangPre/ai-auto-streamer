"""Imported song lyrics are saved defaults, independent of playback and bans.

Only synthetic lyric text and temporary media are used; no provider calls.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from scripts.import_prepared_songs import import_prepared, read_audio
from src.config import Config
from src.lyrics import parse_lrc
from src.pipeline import Pipeline
from src.singing import SingingStudio
from tests.test_lyrics import mp3
import src.server as server


TRACKS = [
    ('cdb8633cfca14ce3af22d15f33bc5ad6', 'BV1D64y1Z7Ko', '起风了'),
    ('9ad59f1d922a4176a417d5984d1f3dac', 'BV1y82uB2ET4', '小幸运'),
    ('876d536a30644003a66929a59b7af9d2', 'BV1A3411J7HX', '告白气球'),
    ('10dec8e228f74084aea80e6902e2225c', 'BV1f59QBGEhx', '大鱼'),
    ('2937303304384eaeb97334368c3f8642', 'BV14nrkBPEBE', '海屿你'),
]


class LyricDefaultAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Config()
        self.config.llm.provider = 'mock'
        self.config.tts.engine = 'none'
        self.config.platform.type = 'text'
        self.config.idle.enabled = False
        self.config.idle.self_talk = False
        for target in (patch('src.server.Config.load', return_value=self.config),
                       patch('src.server.settings_path', return_value=self.root / 'studio-settings.json'),
                       patch('src.danmaku.sys.stdin.isatty', return_value=False)):
            target.start()
            self.addCleanup(target.stop)
        self.client = TestClient(server.app, base_url='http://localhost',
                                 headers={'X-Studio-API': '1', 'Origin': 'http://localhost'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.studio = server.app.state.singing
        self.studio.root.mkdir(parents=True, exist_ok=True)
        self.saved = {}
        self.audio_hashes = {}
        for index, (token, bvid, title) in enumerate(TRACKS):
            audio_path = self.studio.root / (token + '.mp3')
            guide_path = self.studio.root / (token + '-guide.mp3')
            audio_path.write_bytes(mp3())
            guide_path.write_bytes(mp3())
            item = {'id': token, 'title': title, 'profile_id': 'astra', 'created': index,
                    'duration': 42., 'bytes': audio_path.stat().st_size,
                    'guide_bytes': guide_path.stat().st_size, 'source': 'upload',
                    'model': 'offline-voice-conversion', 'voice': 'Saved Astra voice',
                    'url': '/api/singing/audio/' + token,
                    'guide_url': '/api/singing/audio/' + token + '/guide',
                    'conversion_provenance': {'bvid': bvid, 'import_key': str(index) * 64,
                                              'settings': {'length_adjust': 1., 'semitones': 0}}}
            self.saved[token] = item
            (self.studio.root / (token + '.json')).write_text(json.dumps(item), encoding='utf-8')
            for path in (audio_path, guide_path):
                self.audio_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        profile = server.app.state.characters.profiles['astra']
        profile.module_options['singing'] = {'blocked_titles': ['告白气球', '海屿你']}

    def attach(self, token, index):
        lrc = (f'[ti:本地对齐测试 {index}]\n[00:00.000]\n'
               f'[00:12.500]测试片段 {index}\n[00:16.250]\n'
               f'[00:25.125]另一测试片段 {index}\n[00:30.500]\n')
        response = self.client.post('/api/singing/lyrics', json={'song_id': token, 'lrc_text': lrc})
        self.assertEqual(response.status_code, 200, response.text)
        return parse_lrc(lrc)

    def test_five_existing_takes_reload_their_own_lyrics_by_default_without_audio_changes(self):
        expected = {token: self.attach(token, index) for index, (token, _, _) in enumerate(TRACKS)}
        # A fresh service reads defaults from disk; no UI selection or enable flag.
        fresh = SingingStudio(self.studio.root, Pipeline(self.config))
        songs = {song['id']: song for song in fresh.catalog()}
        self.assertEqual(set(songs), set(self.saved))
        for token, original in self.saved.items():
            with self.subTest(song=original['title']):
                self.assertEqual(songs[token]['lyrics_cues'], expected[token]['cues'])
                self.assertEqual(songs[token]['lyrics_offset'], 0.)
                for field, value in original.items():
                    self.assertEqual(songs[token][field], value)
                self.assertNotIn('lyrics_enabled', songs[token])
                # Preload and spoken intro are not part of the recorded song clock.
                self.assertEqual(songs[token]['lyrics_cues'][1]['at'], 12.5)
        for path, digest in self.audio_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertIsNone(self.studio.playback)

    def test_lyric_attachment_does_not_unban_a_song_or_modify_character_policy(self):
        original_policy = json.dumps(server.app.state.characters.profiles['astra'].module_options, sort_keys=True)
        for index, (token, _, title) in enumerate(TRACKS):
            self.attach(token, index)
            if title in ('告白气球', '海屿你'):
                response = self.client.post('/api/singing/play', json={'song_id': token})
                self.assertEqual(response.status_code, 403, response.text)
                response = self.client.post('/api/singing/requests', json={'song_id': token})
                self.assertEqual(response.status_code, 403, response.text)
        flags = {song['title']: song['blocked'] for song in self.client.get('/api/singing').json()['songs']}
        self.assertEqual({title for title, blocked in flags.items() if blocked}, {'告白气球', '海屿你'})
        self.assertEqual(json.dumps(server.app.state.characters.profiles['astra'].module_options, sort_keys=True), original_policy)
        self.assertIsNone(self.studio.playback)

    def test_saved_defaults_keep_instrumental_clear_points_and_user_timing_adjustment(self):
        token = TRACKS[0][0]
        parsed = self.attach(token, 0)
        response = self.client.post('/api/singing/lyrics/settings',
                                    json={'song_id': token, 'offset_seconds': -.25})
        self.assertEqual(response.status_code, 200, response.text)
        reloaded = SingingStudio(self.studio.root, Pipeline(self.config)).catalog()
        song = next(value for value in reloaded if value['id'] == token)
        self.assertEqual(song['lyrics_offset'], -.25)
        self.assertEqual(song['lyrics_cues'], parsed['cues'])
        self.assertEqual([cue['at'] for cue in song['lyrics_cues'] if not cue['text']], [0., 16.25, 30.5])
        self.assertEqual(song['duration'], self.saved[token]['duration'])

    def test_song_backup_contains_default_lyrics_and_original_take_provenance(self):
        token = TRACKS[0][0]
        parsed = self.attach(token, 0)
        result = self.studio.backup('astra')
        path = self.studio.root.with_name(self.studio.root.name + '-backups') / 'astra' / (result['id'] + '.zip')
        with zipfile.ZipFile(path) as archive:
            stored = json.loads(archive.read('songs/' + token + '.json'))
            self.assertEqual(stored['lyrics_cues'], parsed['cues'])
            self.assertEqual(stored['conversion_provenance'], self.saved[token]['conversion_provenance'])
            for suffix in ('.mp3', '-guide.mp3'):
                member = 'songs/' + token + suffix
                self.assertEqual(hashlib.sha256(archive.read(member)).hexdigest(),
                                 self.audio_hashes[self.studio.root / (token + suffix)])


class PreparedReimportLyricDefaultTests(unittest.TestCase):
    def test_idempotent_prepared_reimport_keeps_edited_default_lyrics_and_same_song_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / 'approved.wav'
            reference.write_bytes(b'approved fixture bytes')
            digest = hashlib.sha256(reference.read_bytes()).hexdigest()
            reference_metadata = root / 'reference.json'
            reference_metadata.write_text(json.dumps({'path': str(reference), 'approved': True,
                'candidate_id': 'current-profile', 'profile_id': 'astra', 'reference_sha256': digest}), encoding='utf-8')
            audio = root / 'mixed.mp3'
            guide = root / 'vocals.mp3'
            audio.write_bytes(mp3())
            guide.write_bytes(mp3())
            manifest = root / 'prepared.json'
            manifest.write_text(json.dumps({'voice_candidate': 'current-profile', 'reference_sha256': digest,
                'tracks': [{'bvid': TRACKS[0][1], 'title': TRACKS[0][2], 'ready': True,
                            'mix_mp3': str(audio), 'vocals_mp3': str(guide),
                            'duration_seconds': read_audio(audio)['duration'],
                            'conversion': {'engine': 'Seed-VC', 'diffusion_steps': 50}}]}), encoding='utf-8')
            library = root / 'songs'
            first = import_prepared(manifest, library, reference_metadata)
            token = first['imported'][0]['song_id']
            studio = SingingStudio(library, Pipeline(Config()))
            studio.attach_lyrics(token, '[00:00.100]默认测试\n[00:00.900]', 'astra')
            studio.set_lyrics_offset(token, .125, 'astra')
            before = (library / (token + '.json')).read_bytes()
            second = import_prepared(manifest, library, reference_metadata)
            self.assertEqual(second['imported'], [])
            self.assertEqual(second['skipped'][0]['song_id'], token)
            self.assertEqual((library / (token + '.json')).read_bytes(), before)
            self.assertIsNone(second['backup'])
            self.assertFalse(second['playback_started'])


if __name__ == '__main__':
    unittest.main()
