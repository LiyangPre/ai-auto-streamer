"""LRC edits are bounded, moderated, character-owned, and audio-clock neutral."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import lameenc

from src.config import Config
from src.lyrics import parse_lrc, save_song_metadata
from src.pipeline import Pipeline
from src.singing import SingingStudio
import src.server as server


def mp3():
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(24000)
    encoder.set_channels(1)
    return bytes(encoder.encode(b'\0' * 48000) + encoder.flush())


class LRCParserTests(unittest.TestCase):
    def test_bom_metadata_offsets_repeats_bilingual_and_blank_clears(self):
        parsed = parse_lrc('\ufeff[ti:小小测试]\r\n[ar:Astra]\r\n[offset:+500]\r\n'
                           '[00:10.250][00:20.25]第一句\r\n[00:10.250]First line\r\n'
                           '[00:10.250]第一句\r\n[00:25.000]\r\n')
        self.assertEqual(parsed['metadata'], {'title': '小小测试', 'artist': 'Astra'})
        self.assertEqual(parsed['offset_seconds'], -.5)
        self.assertEqual(parsed['cues'], [{'at': 10.25, 'text': '第一句'},
                                          {'at': 10.25, 'text': 'First line'},
                                          {'at': 20.25, 'text': '第一句'}, {'at': 25., 'text': ''}])

    def test_decimal_precision_hours_and_negative_offset_are_separate(self):
        value = parse_lrc('[offset:-1250]\n[00:12.3]a\n[00:12.03]b\n[00:12.003]c\n[01:00:00.000]end')
        self.assertEqual([cue['at'] for cue in value['cues']], [12.003, 12.03, 12.3, 3600])
        self.assertEqual(value['offset_seconds'], 1.25)

    def test_native_lrc_offset_is_normalized_to_the_positive_later_ui_convention(self):
        early = parse_lrc('[offset:+500]\n[00:10.00]earlier')
        later = parse_lrc('[offset:-500]\n[00:10.00]later')
        self.assertEqual(early['offset_seconds'], -.5)
        self.assertEqual(later['offset_seconds'], .5)
        self.assertEqual(early['cues'][0]['at'], 10.)
        self.assertEqual(later['cues'][0]['at'], 10.)

    def test_invalid_timestamps_and_offsets_do_not_silently_publish(self):
        for text in ('[00:60]bad', '[00:12.1234]bad', '[61:00]bad', '[01:60:00]bad',
                     '[00:01bad', '[offset:nan]\n[00:01]x', '[offset:30001]\n[00:01]x',
                     '[offset:1][offset:2]\n[00:01]x', '[00:01]\n[00:02]', 'plain lyrics'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_lrc(text)

    def test_payload_character_line_and_expansion_limits(self):
        for text in ('你' * 23000, 'a' * 30001, '[00:01]' + 'a' * 301,
                     '\n' * 2000 + '[00:01]x', '[00:01]' * 2001 + 'x',
                     '[00:01][00:02]' + 'a' * 300 + '\n'
                     + ''.join(f'[{n // 60:02d}:{n % 60:02d}]' for n in range(3, 103)) + 'b' * 300,
                     '[00:01]bad\0text'):
            with self.subTest(length=len(text)), self.assertRaises(ValueError):
                parse_lrc(text)

    def test_bracketed_lyric_text_and_html_are_kept_literal(self):
        parsed = parse_lrc('[by:local]\n[00:01][副歌] <b>原样文本</b>')
        self.assertEqual(parsed['cues'][0]['text'], '[副歌] <b>原样文本</b>')


class LyricStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = Config()
        config.safety.blocked_keywords = ['LYRIC_BANNED']
        self.pipeline = Pipeline(config)
        self.studio = SingingStudio(self.root, self.pipeline)
        self.token = 'a' * 32
        self.audio = self.root / (self.token + '.mp3')
        self.audio.write_bytes(mp3())
        self.item = {'id': self.token, 'profile_id': 'astra', 'title': 'Local test',
                     'duration': 40, 'created': 1, 'bytes': self.audio.stat().st_size,
                     'url': '/api/singing/audio/' + self.token, 'lyrics': 'existing untimed text'}
        (self.root / (self.token + '.json')).write_text(json.dumps(self.item), encoding='utf-8')
        self.audio_hash = hashlib.sha256(self.audio.read_bytes()).hexdigest()

    def test_attach_offset_and_remove_preserve_audio_and_existing_untimed_lyrics(self):
        first = self.studio.attach_lyrics(self.token, '[offset:-500]\n[00:01]测试歌词', 'astra')
        self.assertEqual(first['lyrics_offset'], .5)
        reloaded = SingingStudio(self.root, self.pipeline).catalog()[0]
        self.assertEqual(reloaded['lyrics_cues'], [{'at': 1., 'text': '测试歌词'}])
        updated = self.studio.set_lyrics_offset(self.token, 1.25, 'astra')
        self.assertEqual(updated['lyrics_offset'], 1.25)
        removed = self.studio.remove_lyrics(self.token, 'astra')
        self.assertNotIn('lyrics_cues', removed)
        self.assertEqual(removed['lyrics'], 'existing untimed text')
        self.assertEqual(hashlib.sha256(self.audio.read_bytes()).hexdigest(), self.audio_hash)

    def test_character_ownership_moderation_and_bad_offset_preserve_metadata(self):
        metadata = self.root / (self.token + '.json')
        prior = metadata.read_bytes()
        for operation in (lambda: self.studio.attach_lyrics(self.token, '[00:01]x', 'claude'),
                          lambda: self.studio.attach_lyrics('../escape', '[00:01]x', 'astra'),
                          lambda: self.studio.attach_lyrics(self.token, '[00:01]LYRIC_BANNED', 'astra'),
                          lambda: self.studio.attach_lyrics(self.token, '[00:01]valid\n[offset:nan]', 'astra')):
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(metadata.read_bytes(), prior)
        self.studio.attach_lyrics(self.token, '[00:01]x', 'astra')
        prior = metadata.read_bytes()
        for value in (float('nan'), float('inf'), True, '1', 31, -31):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.studio.set_lyrics_offset(self.token, value, 'astra')
            self.assertEqual(metadata.read_bytes(), prior)

    def test_storage_replace_failure_leaves_the_old_song_metadata_intact(self):
        metadata = self.root / (self.token + '.json')
        prior = metadata.read_bytes()
        with patch('src.lyrics.os.replace', side_effect=PermissionError('locked')):
            with self.assertRaises(OSError):
                self.studio.attach_lyrics(self.token, '[00:01]test', 'astra')
        self.assertEqual(metadata.read_bytes(), prior)
        self.assertEqual(list(self.root.glob('.*-lyrics-*.tmp')), [])


class LyricAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cfg = Config()
        cfg.llm.provider = 'mock'
        cfg.tts.engine = 'none'
        cfg.platform.type = 'text'
        cfg.idle.enabled = False
        for p in (patch('src.server.Config.load', return_value=cfg),
                  patch('src.server.settings_path', return_value=self.root / 'studio-settings.json'),
                  patch('src.danmaku.sys.stdin.isatty', return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(server.app, base_url='http://localhost',
                                 headers={'X-Studio-API': '1', 'Origin': 'http://localhost'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.studio = server.app.state.singing
        self.studio.root.mkdir(parents=True, exist_ok=True)
        self.token = 'b' * 32
        self.song = {'id': self.token, 'title': 'test', 'profile_id': 'astra', 'duration': 40,
                     'created': 1, 'voice': 'local', 'bytes': 100,
                     'url': '/api/singing/audio/' + self.token}
        (self.studio.root / (self.token + '.json')).write_text(json.dumps(self.song), encoding='utf-8')
        (self.studio.root / (self.token + '.mp3')).write_bytes(mp3())

    def test_local_upload_offset_and_removal_update_live_metadata_without_restarting_audio(self):
        self.assertEqual(self.client.post('/api/singing/play', json={'song_id': self.token}).status_code, 200)
        before = self.studio.playback.copy()
        with patch.object(self.studio.pipeline, '_emit', new_callable=AsyncMock) as emit:
            response = self.client.post('/api/singing/lyrics', json={
                'song_id': self.token, 'lrc_text': '[00:01]同步测试\n[00:02.50]第二行'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.studio.playback['id'], before['id'])
            self.assertEqual(self.studio.playback['starts_at'], before['starts_at'])
            self.assertEqual(emit.call_args.kwargs['type'], 'singing_lyrics_updated')
            self.assertEqual(len(self.studio.playback['song']['lyrics_cues']), 2)
            response = self.client.post('/api/singing/lyrics/settings', json={
                'song_id': self.token, 'offset_seconds': 1.5})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.studio.playback['song']['lyrics_offset'], 1.5)
            response = self.client.post('/api/singing/lyrics/remove', json={'song_id': self.token})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.studio.playback['song']['lyrics_cues'], [])
            self.assertEqual(self.studio.playback['starts_at'], before['starts_at'])

    def test_all_lyric_writes_require_local_header_and_origin(self):
        bodies = {'/lyrics': {'song_id': self.token, 'lrc_text': '[00:01]test'},
                  '/lyrics/settings': {'song_id': self.token, 'offset_seconds': 0},
                  '/lyrics/remove': {'song_id': self.token}}
        for path, body in bodies.items():
            for headers in ({'Origin': 'https://elsewhere.example'}, {'X-Studio-API': ''}):
                response = self.client.post('/api/singing' + path, json=body, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)

    def test_invalid_and_oversized_uploads_do_not_replace_existing_lyrics(self):
        valid = {'song_id': self.token, 'lrc_text': '[00:01]test'}
        self.assertEqual(self.client.post('/api/singing/lyrics', json=valid).status_code, 200)
        metadata = self.studio.root / (self.token + '.json')
        prior = metadata.read_bytes()
        for body in ({**valid, 'lrc_text': 'plain text'}, {**valid, 'unknown': 'value'},
                     {**valid, 'lrc_text': '你' * 23000}, {**valid, 'song_id': 'c' * 32}):
            response = self.client.post('/api/singing/lyrics', json=body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(metadata.read_bytes(), prior)
        response = self.client.post('/api/singing/lyrics', content=b'x' * (400 * 1024 + 1),
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 413)
        for value in (float('nan'), float('inf'), True, '1', 31):
            response = self.client.post('/api/singing/lyrics/settings',
                                        content=json.dumps({'song_id': self.token, 'offset_seconds': value}),
                                        headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(metadata.read_bytes(), prior)
