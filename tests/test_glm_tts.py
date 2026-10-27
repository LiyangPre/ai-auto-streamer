import asyncio
import base64
import json
import math
import struct
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.config import TTSConfig
from src.tts import TTSEngine


def sse(event):
    return ('data: ' + json.dumps(event) + '\n\n').encode()


def audio(data, rate=24000):
    return sse({'choices': [{'delta': {'return_sample_rate': rate,
                                       'content': base64.b64encode(data).decode()}}]})


STOP = sse({'choices': [{'finish_reason': 'stop'}]})
PCM = b''.join(struct.pack('<h', int(6000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(24000))


class GLMVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_encodes_before_completion_and_snapshots_voice(self):
        gate = asyncio.Event()
        calls = []

        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield audio(PCM[:1])
                yield audio(PCM[1:])
                await gate.wait()
                yield STOP

        def handle(request):
            calls.append(json.loads(request.content))
            self.assertEqual(request.headers['Authorization'], 'Bearer test-only-glm')
            self.assertEqual(str(request.url), 'https://open.bigmodel.cn/api/paas/v4/audio/speech')
            return httpx.Response(200, stream=Chunks())

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'GLM_API_KEY': 'test-only-glm'}):
            engine = TTSEngine(TTSConfig(engine='glm-tts', rate='-4%'), directory)
            engine._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            url = await engine.create_stream('今天有点累。')
            job = engine.jobs[url.rsplit('/', 1)[-1]]
            engine.config.glm_voice = 'xiaochen'
            reader = engine.stream_bytes(job.path.name)
            try:
                first = await asyncio.wait_for(anext(reader), 2)
                self.assertGreater(len(first), 100)
                self.assertEqual(first[0], 255)  # MPEG frame sync
                self.assertEqual(first[1] & 0xE0, 0xE0)
                self.assertFalse(job.done)
                gate.set()
                await job.task
                self.assertFalse(job.error)
                self.assertGreater(job.path.stat().st_size, len(first))
                self.assertEqual(calls[0]['voice'], 'tongtong')
                self.assertEqual(calls[0]['speed'], .96)
                self.assertEqual(calls[0]['response_format'], 'pcm')
                self.assertNotIn('pitch', calls[0])
                self.assertNotIn('emotion', calls[0])
                engine.config.glm_voice = 'tongtong'
                self.assertTrue((await engine.create_stream('今天有点累。')).startswith('/audio/'))
                self.assertEqual(len(calls), 1)
                engine.config.glm_voice = 'xiaochen'
                self.assertNotIn(engine._cache_key('今天有点累。', engine._voice_params(None)), engine.cache)
            finally:
                gate.set()
                await reader.aclose()
                await engine.close()

    async def test_corrupt_rejected_and_incomplete_audio_is_not_cached(self):
        bodies = [
            (401, b'private-provider-content'),
            (200, sse({'error': {'code': '1214', 'message': 'private-provider-content'}})),
            (200, audio(PCM)),
            (200, audio(b'\x01') + STOP),
            (200, audio(PCM) + audio(PCM, 48000) + STOP),
            (200, sse({'choices': [{'delta': {'content': 'invalid base64!'}}]}) + STOP),
            (200, audio(PCM) + sse({'choices': [{'finish_reason': 'length'}]})),
            (200, STOP),
        ]
        for status, body in bodies:
            with self.subTest(status=status, size=len(body)), tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'GLM_API_KEY': 'test-only-glm'}):
                engine = TTSEngine(TTSConfig(engine='glm-tts'), directory)
                engine._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body)))
                with self.assertLogs('src.tts', level='ERROR') as logs:
                    self.assertIsNone(await engine.synthesize('你好。'))
                self.assertEqual(len(engine.cache), 0)
                self.assertEqual(list(engine.output_dir.glob('*.mp3')), [])
                self.assertNotIn('private-provider-content', str(logs.output))
                self.assertNotIn('test-only-glm', str(logs.output))
                await engine.close()

    async def test_missing_glm_key_does_not_use_another_provider_key(self):
        with patch.dict('os.environ', {'GLM_API_KEY': '', 'MINIMAX_API_KEY': 'other-key', 'OPENAI_API_KEY': 'other-key'}):
            self.assertFalse(TTSEngine.provider_ready(TTSConfig(engine='glm-tts')))
        with patch.dict('os.environ', {'GLM_API_KEY': 'test-only-glm'}):
            self.assertTrue(TTSEngine.provider_ready(TTSConfig(engine='glm-tts')))


if __name__ == '__main__':
    unittest.main()
