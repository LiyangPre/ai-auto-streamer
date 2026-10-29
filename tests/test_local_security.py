"""Browser CSRF/DNS rebinding boundary, using only local mocked services."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from starlette.testclient import WebSocketDenialResponse

from src.config import Config
from src.server import app


class LocalBoundaryTests(unittest.TestCase):
    def setUp(self):
        config = Config()
        config.llm.provider = 'mock'
        config.tts.engine = 'none'
        config.idle.enabled = False
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for patcher in (patch('src.server.Config.load', return_value=config),
                        patch('src.server.settings_path', return_value=Path(self.directory.name) / 'settings.json'),
                        patch('src.danmaku.sys.stdin.isatty', return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-Studio-API': '1', 'Origin': 'http://localhost'}

    def test_untrusted_host_is_rejected_before_local_controller(self):
        for path in ('/control', '/api/studio', '/api/memory'):
            with self.subTest(path=path):
                response = self.client.get(path, headers={'Host': 'rebound.attacker.example',
                                                         'Origin': 'http://rebound.attacker.example',
                                                         'X-Studio-API': '1'})
                self.assertEqual(response.status_code, 400)

    def test_private_gets_require_controller_header(self):
        for path in ('/api/status', '/api/studio', '/api/media', '/api/memory',
                     '/api/config', '/api/characters', '/api/modules', '/api/stories',
                     '/api/singing', '/api/connections'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get('/api/studio', headers=self.headers).status_code, 200)

    def test_write_guards_run_before_model_or_paid_voice_work(self):
        # Empty bodies would otherwise fail model validation. These must stop
        # at the browser boundary before invoking any endpoint/provider.
        for path in ('/api/studio', '/api/direct-reply', '/api/voice-preview',
                     '/api/characters', '/api/characters/activate', '/api/danmaku',
                     '/api/test-event', '/api/connections', '/api/connections/test',
                     '/api/modules/story/presentation', '/api/modules/story/complete',
                     '/api/modules/story/resume-welcome', '/api/stories/settings',
                     '/api/stories/prepare', '/api/stories/cancel', '/api/stories/play',
                     '/api/stories/stop', '/api/singing/import-prepared', '/api/singing/import',
                     '/api/singing/prepare', '/api/singing/cancel', '/api/singing/play',
                     '/api/singing/stop', '/api/singing/backup'):
            with self.subTest(path=path):
                response = self.client.post(path, json={})
                self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post('/api/media/music', content=b'not-a-file',
                                    headers={'X-Filename': 'test.mp3'})
        self.assertEqual(response.status_code, 403)

    def test_foreign_origin_same_site_and_cross_site_fetch_are_rejected(self):
        for extra in ({'Origin': 'https://attacker.example'}, {'Origin': 'null'},
                      {'Origin': 'http://localhost:8001'}, {'Sec-Fetch-Site': 'cross-site'},
                      {'Sec-Fetch-Site': 'same-site'}, {'Referer': 'https://attacker.example/page'}):
            with self.subTest(extra=extra):
                response = self.client.get('/api/memory', headers={**self.headers, **extra})
                self.assertEqual(response.status_code, 403)

    def test_loopback_host_variants_and_ipv6_work(self):
        for origin in ('http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000'):
            with self.subTest(origin=origin):
                response = self.client.get(origin + '/api/studio',
                                           headers={'X-Studio-API': '1', 'Origin': origin})
                self.assertEqual(response.status_code, 200, response.text)

    def test_non_loopback_peer_is_rejected_even_with_controller_headers(self):
        remote = TestClient(app, base_url='http://localhost', client=('192.0.2.23', 12345))
        self.assertEqual(remote.get('/api/studio', headers=self.headers).status_code, 403)
        self.assertEqual(remote.get('/audio/missing.mp3').status_code, 403)

    def test_media_elements_need_no_custom_header_but_cross_origin_is_denied(self):
        # Missing local resources should reach normal 404 handling, not a 403
        # caused by the custom API header unavailable to <audio>/<img> elements.
        for path in ('/audio/missing.mp3', '/media/missing.png', '/api/voice-stream/missing.mp3',
                     '/api/stories/audio/astra/' + 'a' * 32, '/api/singing/audio/' + 'a' * 32):
            with self.subTest(path=path):
                response = self.client.get(path, headers={'Referer': 'http://localhost/control',
                                                         'Sec-Fetch-Site': 'same-origin'})
                self.assertEqual(response.status_code, 404, response.text)
                response = self.client.get(path, headers={'Origin': 'https://attacker.example'})
                self.assertEqual(response.status_code, 403)

    def test_same_origin_websocket_gets_snapshot(self):
        with self.client.websocket_connect('ws://localhost/ws', headers={'Origin': 'http://localhost'}) as socket:
            self.assertEqual(socket.receive_json()['type'], 'status')

    def test_websocket_rejects_missing_foreign_and_wrong_port_origins(self):
        for origin in (None, '', 'null', 'https://attacker.example', 'http://localhost:8001'):
            for path in ('/ws', '/ws/music?role=output'):
                with self.subTest(origin=origin, path=path):
                    headers = {} if origin is None else {'Origin': origin}
                    with self.assertRaises(WebSocketDisconnect) as error:
                        with self.client.websocket_connect('ws://localhost' + path, headers=headers):
                            pass
                    self.assertEqual(error.exception.code, 1008)

    def test_websocket_rebinding_host_cannot_read_snapshot(self):
        with self.assertRaises(WebSocketDenialResponse) as error:
            with self.client.websocket_connect('ws://rebound.attacker.example/ws',
                                               headers={'Origin': 'http://rebound.attacker.example'}):
                pass
        self.assertEqual(error.exception.status_code, 400)


if __name__ == '__main__':
    unittest.main()
