import unittest

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.music_capture import MusicCaptureHub
from src.config import SceneConfig


class MusicCaptureTests(unittest.TestCase):
    def setUp(self):
        self.hub = MusicCaptureHub()
        app = FastAPI()

        @app.websocket('/ws/music')
        async def endpoint(socket: WebSocket):
            await self.hub.connect(socket)

        self.client = TestClient(app)

    def connect(self, role, origin='http://testserver'):
        return self.client.websocket_connect('/ws/music?role=' + role, headers={'origin': origin})

    def test_audio_signaling_and_source_replacement(self):
        with self.connect('output') as output:
            peer = output.receive_json()['id']
            for _ in range(2):
                with self.connect('source') as source:
                    self.assertEqual(source.receive_json()['type'], 'welcome')
                    self.assertEqual(source.receive_json(), {'type': 'receiver', 'id': peer})
                    source.send_json({'type': 'offer', 'id': peer, 'sdp': 'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'})
                    self.assertEqual(output.receive_json()['type'], 'offer')
                    output.send_json({'type': 'answer', 'sdp': 'v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'})
                    answer = source.receive_json()
                    self.assertEqual((answer['type'], answer['id']), ('answer', peer))
                self.assertEqual(output.receive_json(), {'type': 'source-off'})
        self.assertEqual(self.hub.outputs, {})
        self.assertIsNone(self.hub.source)

    def test_output_join_leave_and_single_publisher(self):
        with self.connect('source') as source:
            source.receive_json()
            with self.connect('source') as duplicate:
                self.assertEqual(duplicate.receive_json(), {'type': 'error', 'reason': 'busy'})
            with self.connect('output') as output:
                peer = output.receive_json()['id']
                self.assertEqual(source.receive_json(), {'type': 'receiver', 'id': peer})
            self.assertEqual(source.receive_json(), {'type': 'receiver-left', 'id': peer})

    def test_cross_origin_unknown_role_and_video_are_rejected(self):
        for role, origin in [('source', 'https://unrelated.example'), ('invalid', 'http://testserver'), ('output', '')]:
            with self.assertRaises(WebSocketDisconnect):
                with self.connect(role, origin):
                    pass
        with self.connect('source') as source:
            source.receive_json()
            source.send_json({'type': 'offer', 'sdp': 'm=audio 9\r\nm=video 9\r\n', 'id': 'missing'})
            with self.assertRaises(WebSocketDisconnect):
                source.receive_json()

    def test_music_source_is_backward_compatible_and_strict(self):
        self.assertEqual(SceneConfig().music_source, 'file')
        saved = SceneConfig(music_source='system', music_asset='a' * 32).model_dump_json()
        restored = SceneConfig.model_validate_json(saved)
        self.assertEqual(restored.music_source, 'system')
        self.assertEqual(restored.music_asset, 'a' * 32)
        with self.assertRaises(ValueError):
            SceneConfig(music_source='https://external.example')
