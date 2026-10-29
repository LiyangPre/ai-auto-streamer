import asyncio
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from src.config import Config, TTSConfig
from src.danmaku import DanmakuMessage
from src.llm import CallParams
from src.pipeline import Pipeline
from src.speech_text import chat_readback
from src.tts import TTSEngine


def chat(text):
    return DanmakuMessage(text, "观众", 101, time.time())


class Answer:
    def __init__(self):
        self.calls = []

    async def chat(self, text, params):
        self.calls.append(text)
        return "先歇一会儿。我在听。", "normal"

    async def chat_stream(self, text, params):
        self.calls.append(text)
        yield "先歇", "normal"
        yield "一会儿。", None
        yield "我在听。", None


class Voice:
    def __init__(self):
        self.calls = []

    async def synthesize(self, text, emotion):
        self.calls.append(text)
        return "/audio/test.mp3"


class ChatReadbackTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self):
        config = Config()
        config.router.enabled = False
        config.character.read_chat = True  # Explicit opt-in; production default is off.
        config.reply_interval.enabled = False
        config.tts.progressive_audio = False
        pipeline = Pipeline(config)
        pipeline.llm = Answer()
        pipeline.tts = Voice()
        return pipeline

    def test_core_preserves_negation_and_removes_addresses_and_links(self):
        self.assertEqual(chat_readback([chat("主播，你好，请问我不想加班怎么办？")]), "我不想加班怎么办？")
        self.assertEqual(chat_readback([chat("@露娜 我今天不开心。 https://example.com")]), "我今天不开心。")
        self.assertEqual(chat_readback([chat("http://example.com")]), "")
        self.assertEqual(chat_readback([chat("今天有点累")]), "今天有点累。")
        self.assertLessEqual(len(chat_readback([chat("很长的弹幕" * 20)])), 34)

    async def test_readback_only_changes_audio_and_uses_one_model_call(self):
        p = self.pipeline()
        p.config.llm.stream = False
        await p._handle_speech([chat("今天有点累")])
        events = [p.result_queue.get_nowait() for _ in range(p.result_queue.qsize())]
        reply = next(e for e in events if e['type'] == 'response')
        self.assertEqual(reply['text'], "先歇一会儿。我在听。")
        self.assertEqual(p.tts.calls, ["今天有点累。先歇一会儿。我在听。"])
        self.assertEqual(p.llm.calls, ["今天有点累"])
        self.assertEqual(p.metrics['llm_calls'], 1)

    async def test_stream_combines_prefix_with_first_answer_sentence_once(self):
        p = self.pipeline()
        await p._handle_speech([chat("今天有点累")])
        self.assertEqual(p.tts.calls, ["今天有点累。先歇一会儿。", "我在听。"])
        events = [p.result_queue.get_nowait() for _ in range(p.result_queue.qsize())]
        self.assertEqual(next(e for e in events if e['type'] == 'response_end')['full_text'], "先歇一会儿。我在听。")
        self.assertEqual(len(p.llm.calls), 1)

    async def test_full_audio_mode_and_readback_switch(self):
        p = self.pipeline()
        p.config.tts.sentence_stream = False
        await p._handle_speech([chat("今天有点累")])
        self.assertEqual(p.tts.calls, ["今天有点累。先歇一会儿。我在听。"])
        p.config.character.read_chat = False
        await p._handle_speech([chat("今天有点累")])
        self.assertEqual(p.tts.calls[-1], "先歇一会儿。我在听。")

    async def test_batch_reads_only_latest_selected_core_and_templates_also_work(self):
        p = self.pipeline()
        await p._handle_speech([chat("好困"), chat("今天有点累")])
        self.assertTrue(p.tts.calls[0].startswith("今天有点累。"))
        self.assertNotIn("好困", p.tts.calls[0])
        p = self.pipeline()
        p.config.router.enabled = True
        await p._handle_speech([chat("你好")])
        self.assertEqual(p.metrics['llm_calls'], 0)
        self.assertTrue(p.tts.calls[0].startswith("你好。"))

    async def test_no_readback_when_output_is_blocked_or_empty(self):
        p = self.pipeline()
        class Blocked:
            async def chat_stream(self, text, params):
                yield '自杀', 'sad'
        p.llm = Blocked()
        await p._reply('测试', CallParams(), True, speech_prefix='今天有点累。')
        self.assertEqual(p.tts.calls, [])
        class Empty:
            async def chat(self, text, params):
                return '', 'normal'
        p.llm = Empty()
        await p._reply('测试', CallParams(), False, speech_prefix='今天有点累。')
        self.assertEqual(p.tts.calls, [])


def sse(data):
    return ("data: " + json.dumps(data) + "\n\n").encode()


class MiniMaxDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_progressive_audio_is_shared_not_duplicated_and_settings_are_snapshotted(self):
        gate = asyncio.Event()
        calls = []
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({'data': {'audio': b'first'.hex(), 'status': 1}, 'base_resp': {'status_code': 0}})
                await gate.wait()
                yield sse({'data': {'audio': b'last'.hex(), 'status': 1}})
                yield sse({'data': {'audio': b'firstlast'.hex(), 'status': 2}})
        def handle(request):
            calls.append(json.loads(request.content))
            self.assertEqual(request.url.host, 'api.minimax.cn')
            return httpx.Response(200, stream=Chunks())
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MINIMAX_API_KEY': 'test-only'}):
            engine = TTSEngine(TTSConfig(engine='minimax', rate='-4%'), directory)
            engine._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            url = await engine.create_stream('今天有点累。先歇一会儿。')
            job = engine.jobs[url.rsplit('/', 1)[-1]]
            engine.config.minimax_voice = 'female-chengshu'
            first = engine.stream_bytes(job.path.name)
            self.assertEqual(await asyncio.wait_for(anext(first), 1), b'first')
            self.assertFalse(job.done)
            gate.set()
            self.assertEqual(await anext(first), b'last')
            await job.task
            self.assertEqual(job.path.read_bytes(), b'firstlast')
            self.assertEqual(calls[0]['voice_setting']['voice_id'], 'female-yujie')
            self.assertEqual(calls[0]['voice_setting']['speed'], .96)
            self.assertEqual(calls[0]['voice_setting']['emotion'], 'calm')
            self.assertTrue(calls[0]['stream_options']['exclude_aggregated_audio'])
            engine.config.minimax_voice = 'female-yujie'
            self.assertTrue((await engine.create_stream('今天有点累。先歇一会儿。')).startswith('/audio/'))
            self.assertEqual(len(calls), 1)
            await first.aclose()
            await engine.close()

    async def test_rejected_or_truncated_stream_is_not_cached_and_errors_hide_provider_content(self):
        for body in (sse({'base_resp': {'status_code': 1004, 'status_msg': 'secret-token'}}),
                     sse({'data': {'audio': b'partial'.hex(), 'status': 1}})):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MINIMAX_API_KEY': 'test-only'}):
                engine = TTSEngine(TTSConfig(engine='minimax'), directory)
                engine._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
                with self.assertLogs('src.tts', level='ERROR') as logs:
                    self.assertIsNone(await engine.synthesize('测试'))
                self.assertNotIn('secret-token', str(logs.output))
                self.assertEqual(list(engine.output_dir.glob('*.mp3')), [])
                self.assertEqual(len(engine.cache), 0)
                await engine.close()

    async def test_provider_and_voice_are_in_cache_identity_and_inflection_is_small(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = TTSEngine(TTSConfig(rate='-4%', pitch='-3Hz', expressive=True), directory)
            self.assertEqual(engine._voice_params('happy')['pitch'], '-1Hz')
            self.assertEqual(engine._voice_params('normal')['pitch'], '-3Hz')
            self.assertEqual(engine._voice_params('sad')['rate'], '-8%')
            edge = engine._cache_key('你好', {})
            engine.config.engine = 'minimax'
            cloud = engine._cache_key('你好', {})
            engine.config.minimax_voice = 'female-chengshu'
            self.assertNotEqual(edge, cloud)
            self.assertNotEqual(cloud, engine._cache_key('你好', {}))
            self.assertNotIn('<#', engine._spoken_text('你好<#99#>。'))


if __name__ == '__main__':
    unittest.main()
