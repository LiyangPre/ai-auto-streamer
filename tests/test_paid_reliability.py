"""Offline paid-interaction replay; no login, purchase or provider request."""
import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import blivedm.models.web as live_models
from yarl import URL

from src.config import Config, MemoryConfig
from src.danmaku import BilibiliDanmakuCollector, DanmakuMessage
from src.memory import ShortTermMemory
from src.paid_interactions import PaidInbox
from src.pipeline import Pipeline


def event(uid, kind='gift', name=None, text='谢谢', price=1, priority=1, gift='花'):
    return DanmakuMessage(text, name or f'观众{uid}', uid, time.time(), kind,
                          gift_name=gift, gift_num=1, price=price, priority=priority)


class PaidInboxTests(unittest.TestCase):
    def test_combo_owns_records_and_gift_name_count_is_bounded(self):
        inbox = PaidInbox(window=4)
        original = event(1, price=2)
        inbox.offer(original)
        inbox.offer(event(1, price=3, priority=3))
        for index in range(100):
            inbox.offer(event(1, gift=f'礼物{index}'))
        self.assertEqual((original.price, original.priority), (2, 1))
        batch = inbox.take()
        self.assertEqual(len(batch), 1)
        self.assertEqual(sum(batch[0].gifts.values()), 102)
        self.assertLessEqual(len(batch[0].gifts), 16)
        self.assertEqual(batch[0].message.price, 105)

    def test_sc_precedes_guard_and_keeps_each_question(self):
        inbox = PaidInbox(capacity=3)
        inbox.offer(event(1, priority=3))
        # Callers cannot accidentally demote an SC or promote a gift above it.
        inbox.offer(event(2, 'super_chat', text='第一道问题', priority=0))
        inbox.offer(event(2, 'super_chat', text='第二道问题', priority=0))
        first, second = inbox.take(), inbox.take()
        self.assertEqual(first[0].message.content, '第一道问题')
        self.assertEqual(second[0].message.content, '第二道问题')
        self.assertEqual(first[0].message.priority, 4)
        self.assertEqual(inbox.take()[0].message.user_id, 1)

    def test_overload_is_bounded_and_visible_sc_retains_priority(self):
        inbox = PaidInbox(capacity=2, window=0)
        inbox.offer(event(1))
        inbox.offer(event(2))
        self.assertTrue(inbox.offer(event(3, 'super_chat', text='付费问题')))
        for uid in range(4, 1004):
            inbox.offer(event(uid))
        self.assertEqual(len(inbox.items), 2)
        self.assertEqual(inbox.overflow, 1001)
        self.assertEqual(inbox.take()[0].message.content, '付费问题')

    def test_combo_window_and_maximum_wait(self):
        inbox = PaidInbox(window=4)
        with patch('src.paid_interactions.time.monotonic', return_value=100):
            inbox.offer(event(1))
        self.assertFalse(inbox.take(now=103))
        with patch('src.paid_interactions.time.monotonic', return_value=119):
            inbox.offer(event(1))
        self.assertEqual(len(inbox.take(now=120)), 1)


class AnonymousIdentityTests(unittest.TestCase):
    def test_redacted_uids_do_not_dedupe_distinct_names_or_mix_personal_memory(self):
        first = event(0, kind='danmaku', name='甲', text='今天考试')
        second = event(0, kind='danmaku', name='乙', text='今天上班')
        same = event(0, kind='danmaku', name='甲', text='补充')
        self.assertLess(first.user_id, 0)
        self.assertNotEqual(first.user_id, second.user_id)
        self.assertEqual(first.user_id, same.user_id)
        self.assertTrue(first.anonymous)
        pipeline = Pipeline(Config())
        self.assertEqual(len(pipeline._select_speech([first, second])), 2)
        memory = ShortTermMemory(MemoryConfig())
        for message in [first, second]:
            memory.add(message.user_id, message.user_name, message.content)
        # No persistent identity is inferred from nicknames. Global room context
        # remains available, with the same TTL as other messages.
        self.assertEqual(memory.snapshot()['users'], {})
        self.assertFalse(memory.is_returning(first.user_id))
        self.assertTrue(memory.should_welcome_enter(first.user_id))
        self.assertTrue(memory.should_welcome_enter(second.user_id))

    def test_zero_id_direct_memory_call_is_not_personalized(self):
        memory = ShortTermMemory(MemoryConfig())
        memory.add(0, '匿名', '测试')
        self.assertEqual(memory.snapshot()['users'], {})


class PaidPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_performance_keeps_paid_events_without_spawning_waiters(self):
        config = Config()
        config.reply_interval.enabled = False
        config.llm.stream = False
        pipeline = Pipeline(config)
        pipeline.stage.acquire('singing')
        for uid in range(1, 6):
            pipeline.danmaku_queue.offer(event(uid))
        sc = event(10, 'super_chat', text='明天会下雨吗？', priority=4)
        pipeline.danmaku_queue.offer(sc)
        self.assertEqual(len(pipeline._background_tasks), 0)
        self.assertEqual(len(pipeline.paid.items), 6)
        calls = []

        class Model:
            async def chat(self, message, params):
                calls.append((message, params))
                pipeline._running = False
                return '谢谢你，明天出门记得看天气预报。', 'happy'

        pipeline.llm = Model()
        pipeline._running = True
        task = asyncio.create_task(pipeline._paid_loop())
        await asyncio.sleep(.01)
        self.assertFalse(calls)
        self.assertEqual(len(pipeline.paid.items), 6)
        pipeline.stage.release('singing')
        await asyncio.wait_for(task, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn('明天会下雨吗？', calls[0][0])
        self.assertIn('回答', calls[0][1].instruction)
        self.assertEqual(len(pipeline.paid.items), 5)

    async def test_sdk_sc_question_and_authenticated_cookie_scope(self):
        queue = asyncio.Queue()
        config = Config().platform
        config.room_id = 1
        collector = BilibiliDanmakuCollector(config, queue)
        collector._make_handler()._on_super_chat(None, live_models.SuperChatMessage(
            price=30, message='请回答我的问题', uname='甲', uid=1))
        message = queue.get_nowait()
        self.assertEqual((message.msg_type, message.priority, message.content),
                         ('super_chat', 4, '请回答我的问题'))
        captured = []

        class Client:
            def __init__(self, room_id, session):
                captured.append(session)

            def set_handler(self, handler):
                pass

            def start(self):
                collector._running = False

            async def join(self):
                jar = captured[0].cookie_jar
                self.assert_cookie = jar.filter_cookies(URL('https://api.bilibili.com'))
                self.outside_cookie = jar.filter_cookies(URL('https://example.com'))

            async def stop_and_close(self):
                pass

        with patch.dict(os.environ, {config.sessdata_env: 'test-secret-only'}), \
                patch('src.danmaku.blivedm.BLiveClient', Client):
            await collector.start()
        jar = captured[0].cookie_jar
        self.assertEqual(jar.filter_cookies(URL('https://api.bilibili.com'))['SESSDATA'].value,
                         'test-secret-only')
        self.assertFalse(jar.filter_cookies(URL('https://example.com')))
        self.assertTrue(captured[0].closed)


if __name__ == '__main__':
    unittest.main()
