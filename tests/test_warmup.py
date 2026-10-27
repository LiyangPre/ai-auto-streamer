"""Quiet-room scheduling, cancellation and cached direct speech (no cloud calls)."""
import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from src.config import Config
from src.danmaku import DanmakuMessage
from src.pipeline import Pipeline


class WarmupTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self):
        config=Config();config.idle.self_talk=True
        config.reply_interval.enabled=False
        pipeline=Pipeline(config);pipeline._running=True
        pipeline.tts=type('Voice',(),{'synthesize':AsyncMock(return_value='/audio/warmup.mp3')})()
        pipeline.ws_clients.add(object())
        pipeline._last_activity_time=time.time()-46
        return pipeline

    async def test_warmup_speaks_without_llm_and_rotates_lines(self):
        pipeline=self.pipeline();pipeline.llm=AsyncMock()
        self.assertTrue(pipeline._maybe_warmup())
        await pipeline._warmup_task
        event=pipeline.result_queue.get_nowait()
        self.assertEqual((event['type'],event['source'],event['audio_url']),('response','warmup','/audio/warmup.mp3'))
        self.assertEqual(pipeline.metrics['llm_calls'],0)
        self.assertEqual(pipeline.metrics['warmups'],1)
        self.assertFalse(pipeline._maybe_warmup(),'warmups must not repeat immediately')
        lines=[pipeline._last_warmup_line]
        for _ in range(15):
            line=pipeline._warmup_line();self.assertNotEqual(line,lines[-1])
            pipeline._last_warmup_line=line;lines.append(line)
        self.assertEqual(len(set(lines[:8])),8)

    async def test_new_chat_cancels_preparing_warmup_and_releases_reply_slot(self):
        pipeline=self.pipeline();started=asyncio.Event();cancelled=asyncio.Event()
        async def slow_voice(*args):
            started.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:cancelled.set();raise
        pipeline.tts.synthesize=slow_voice
        self.assertTrue(pipeline._maybe_warmup());await started.wait()
        pipeline._handle_speech=AsyncMock()
        chat=DanmakuMessage('你好呀','观众',1,time.time())
        await pipeline._handle_window([chat])
        self.assertTrue(cancelled.is_set());self.assertFalse(pipeline._is_replying)
        self.assertEqual(pipeline._last_reply_time,0,'cancelled warmup must not impose a viewer cooldown')
        self.assertEqual(pipeline.result_queue.get_nowait()['type'],'danmaku')
        self.assertTrue(pipeline.result_queue.empty())
        pipeline._handle_speech.assert_awaited_once()

    async def test_no_warmup_during_activity_without_outputs_or_with_pending_messages(self):
        pipeline=self.pipeline();pipeline._last_activity_time=time.time()
        self.assertFalse(pipeline._maybe_warmup())
        pipeline._last_activity_time=time.time()-46;pipeline.ws_clients.clear()
        self.assertFalse(pipeline._maybe_warmup())
        pipeline.ws_clients.add(object())
        pipeline.danmaku_queue.offer(DanmakuMessage('新的弹幕','观众',1,time.time()))
        self.assertFalse(pipeline._maybe_warmup())
        pipeline.danmaku_queue.get_nowait();pipeline.danmaku_queue.task_done()
        pipeline._last_warmup_time=time.time()-60
        self.assertFalse(pipeline._maybe_warmup(),'repeat interval is separate from the quiet delay')
        pipeline._last_warmup_time=0
        pipeline.config.idle.self_talk=False;self.assertFalse(pipeline._maybe_warmup())

    async def test_chat_arriving_during_synthesis_discards_stale_ready_audio(self):
        pipeline=self.pipeline()
        async def new_message(*args):
            pipeline._last_activity_time=time.time()
            return '/audio/stale.mp3'
        pipeline.tts.synthesize=new_message
        self.assertTrue(pipeline._maybe_warmup());await pipeline._warmup_task
        self.assertTrue(pipeline.result_queue.empty())
        self.assertEqual(pipeline.metrics['warmups'],0)
