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


def audio(data):
    return sse({'choices': [{'delta': {'audio': {'data': base64.b64encode(data).decode()}}}]})


STOP = sse({'choices': [{'finish_reason': 'stop'}]})
PCM = b''.join(struct.pack('<h', int(6000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(24000))


class MiMoVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_arrives_before_completion_and_direction_is_not_spoken(self):
        gate = asyncio.Event()
        calls = []

        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({'choices': []})  # Optional usage/heartbeat event.
                yield audio(PCM[:1])
                yield audio(PCM[1:])
                await gate.wait()
                yield STOP

        def handle(request):
            self.assertEqual(str(request.url), 'https://api.xiaomimimo.com/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-only-mimo')
            calls.append(json.loads(request.content))
            return httpx.Response(200, stream=Chunks())

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MIMO_API_KEY': 'test-only-mimo'}):
            engine = TTSEngine(TTSConfig(engine='mimo-tts', mimo_model='mimo-v2.5-tts', mimo_style='清冷从容，略带气声。'), directory)
            engine._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            url = await engine.create_stream('今天累了怎么办？适当休息就好。')
            job = engine.jobs[url.rsplit('/', 1)[-1]]
            engine.config.mimo_style = '轻快自然。'
            engine.config.mimo_voice = '冰糖'
            reader = engine.stream_bytes(job.path.name)
            try:
                first = await asyncio.wait_for(anext(reader), 2)
                self.assertGreater(len(first), 100)
                self.assertEqual(first[0], 255)
                self.assertEqual(first[1] & 0xE0, 0xE0)
                self.assertFalse(job.done)
                gate.set()
                await job.task
                self.assertFalse(job.error)
                self.assertGreater(job.path.stat().st_size, len(first))
                self.assertEqual(calls[0]['audio'], {'voice': '茉莉', 'format': 'pcm16'})
                self.assertEqual(calls[0]['messages'], [
                    {'role': 'user', 'content': '清冷从容，略带气声。'},
                    {'role': 'assistant', 'content': '今天累了怎么办？适当休息就好。'}])
                self.assertNotIn('speed', calls[0])
                self.assertNotIn('pitch', calls[0])
                engine.config.mimo_voice = '茉莉'
                engine.config.mimo_style = '清冷从容，略带气声。'
                self.assertTrue((await engine.create_stream('今天累了怎么办？适当休息就好。')).startswith('/audio/'))
                self.assertEqual(len(calls), 1)
                engine.config.mimo_style = '轻快自然。'
                self.assertNotIn(engine._cache_key('今天累了怎么办？适当休息就好。', engine._voice_params(None)), engine.cache)
            finally:
                gate.set()
                await reader.aclose()
                await engine.close()

    async def test_bad_or_truncated_audio_is_not_cached_and_errors_hide_secrets(self):
        responses = [
            (401, b'private-provider-content'),
            (200, sse({'error': {'message': 'private-provider-content'}})),
            (200, audio(PCM)),
            (200, audio(PCM) + b'data: [DONE]\n\n'),  # Completion marker is required.
            (200, audio(b'\x01') + STOP),
            (200, sse({'choices': [{'delta': {'audio': {'data': 'not base64!'}}}]}) + STOP),
            (200, audio(PCM) + sse({'choices': [{'finish_reason': 'length'}]})),
            (200, STOP),
        ]
        for status, body in responses:
            with self.subTest(status=status, size=len(body)), tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MIMO_API_KEY': 'test-only-mimo'}):
                engine = TTSEngine(TTSConfig(engine='mimo-tts'), directory)
                engine._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body)))
                with self.assertLogs('src.tts', level='ERROR') as logs:
                    self.assertIsNone(await engine.synthesize('你好。'))
                self.assertEqual(len(engine.cache), 0)
                self.assertEqual(list(engine.output_dir.glob('*.mp3')), [])
                self.assertNotIn('private-provider-content', str(logs.output))
                self.assertNotIn('test-only-mimo', str(logs.output))
                await engine.close()

    async def test_blank_direction_uses_plain_text_and_keys_are_isolated(self):
        with patch.dict('os.environ', {'MIMO_API_KEY': '', 'GLM_API_KEY': 'other-key'}):
            self.assertFalse(TTSEngine.provider_ready(TTSConfig(engine='mimo-tts')))
        calls = []

        def handle(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, content=audio(PCM) + STOP)

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MIMO_API_KEY': 'test-only-mimo'}):
            engine = TTSEngine(TTSConfig(engine='mimo-tts', mimo_model='mimo-v2.5-tts', mimo_style=''), directory)
            engine._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            self.assertIsNotNone(await engine.synthesize('你好。'))
            self.assertEqual(calls[0]['messages'], [{'role': 'assistant', 'content': '你好。'}])
            await engine.close()

    async def test_voice_design_uses_description_without_preset_and_changes_cache_identity(self):
        calls=[]
        def handle(request):
            calls.append(json.loads(request.content));return httpx.Response(200,content=audio(PCM)+STOP)
        with tempfile.TemporaryDirectory() as directory:
            cfg=TTSConfig(engine='mimo-tts');cfg._api_keys={'mimo-tts':'unit-test-key'}
            engine=TTSEngine(cfg,directory);engine._http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
            self.assertIsNotNone(await engine.synthesize('（轻笑）今天也要好好休息。'))
            self.assertEqual(calls[0]['model'],'mimo-v2.5-tts-voicedesign')
            self.assertEqual(calls[0]['audio'],{'format':'pcm16','optimize_text_preview':False})
            self.assertEqual(calls[0]['messages'][0]['content'],cfg.mimo_style)
            key=engine._cache_key('test',engine._voice_params(None));cfg.mimo_model='mimo-v2.5-tts'
            self.assertNotEqual(key,engine._cache_key('test',engine._voice_params(None)))
            await engine.close()


if __name__ == '__main__':
    unittest.main()
