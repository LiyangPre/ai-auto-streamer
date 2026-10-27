"""Regression coverage for bounded ingress, telemetry, and controller APIs."""
import asyncio
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.config import Config
from src.danmaku import DanmakuMessage, MessageQueue
from src.pipeline import Pipeline
from src.server import app


def message(index, priority=0):
    return DanmakuMessage(str(index), f"viewer-{index}", index + 1, time.time() + index,
                          msg_type="gift" if priority else "danmaku", priority=priority)


class IngressTests(unittest.TestCase):
    def test_flood_is_bounded_and_keeps_latest_chat(self):
        queue = MessageQueue(3)
        for index in range(1000):
            queue.put_nowait(message(index))
        self.assertEqual(queue.qsize(), 3)
        self.assertEqual(queue.dropped, 997)
        self.assertEqual([queue.get_nowait().content for _ in range(3)], ["997", "998", "999"])

    def test_paid_events_survive_chat_overload(self):
        queue = MessageQueue(2)
        queue.offer(message(0, priority=3))
        queue.offer(message(1, priority=2))
        self.assertFalse(queue.offer(message(2)))
        self.assertTrue(queue.offer(message(3, priority=3)))
        self.assertEqual([queue.get_nowait().priority for _ in range(2)], [3, 3])


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_animation_does_not_restart_reply_cooldown(self):
        pipeline = Pipeline(Config())
        pipeline._running = True
        pipeline._last_reply_time = 100
        pipeline._last_activity_time = pipeline._last_idle_time = 100

        async def idle():
            pipeline._running = False

        pipeline.trigger_idle_action = idle
        with patch("src.pipeline.asyncio.sleep", new=AsyncMock()):
            await pipeline._tick_loop()
        self.assertEqual(pipeline._last_reply_time, 100)
        self.assertEqual(pipeline.status()["cooldown_remaining"], 0)

    async def test_direct_text_arrives_before_slow_speech(self):
        config = Config()
        config.reply_interval.enabled = False
        pipeline = Pipeline(config)
        gate = asyncio.Event()

        async def slow_voice(text, emotion):
            await gate.wait()
            return "/audio/voice.mp3"

        pipeline.tts = type("Voice", (), {"synthesize": staticmethod(slow_voice)})()
        task = asyncio.create_task(pipeline._speak_direct("你好呀", "happy"))
        event = await asyncio.wait_for(pipeline.result_queue.get(), timeout=0.2)
        self.assertEqual(event["type"], "response")
        self.assertEqual(event["text"], "你好呀")
        self.assertIsNone(event["audio_url"])
        self.assertFalse(task.done())
        gate.set()
        await task
        audio = pipeline.result_queue.get_nowait()
        self.assertEqual(audio["type"], "audio_segment")
        self.assertEqual(audio["audio_url"], "/audio/voice.mp3")

    async def test_window_is_bounded_even_when_all_events_are_gifts(self):
        config = Config()
        config.danmaku_filter.time_window = 0.02
        config.danmaku_filter.max_queue_size = 2
        pipeline = Pipeline(config)
        for index in range(20):
            pipeline.danmaku_queue.offer(message(index, priority=3))
        pipeline._handle_window = AsyncMock()
        await pipeline._process_with_filter()
        kept = pipeline._handle_window.call_args.args[0]
        # A busy machine may reach the collection deadline before draining all
        # arrivals. Check the latest *consumed* events, not scheduler speed.
        consumed = 20 - pipeline.danmaku_queue.qsize()
        self.assertEqual([item.content for item in kept], [str(consumed - 2), str(consumed - 1)])
        self.assertEqual(pipeline.status()["dropped"], consumed - 2)
        while not pipeline.danmaku_queue.empty():
            pipeline.danmaku_queue.get_nowait()
            pipeline.danmaku_queue.task_done()
        await asyncio.wait_for(pipeline.danmaku_queue.join(), timeout=0.1)

    async def test_status_reports_remaining_cooldown(self):
        config = Config()
        config.reply_interval.cooldown = 3
        pipeline = Pipeline(config)
        pipeline._last_reply_time = time.time() - 1
        self.assertAlmostEqual(pipeline.status()["cooldown_remaining"], 2, delta=0.1)
        config.reply_interval.enabled = False
        self.assertEqual(pipeline.status()["cooldown_remaining"], 0)

    async def test_slow_client_does_not_delay_healthy_client(self):
        pipeline = Pipeline(Config())
        fast = AsyncMock()
        delivered = asyncio.Event()
        fast.send_json.side_effect = lambda payload: delivered.set()
        slow = AsyncMock()

        async def stall(payload):
            await asyncio.sleep(20)

        slow.send_json.side_effect = stall
        pipeline.ws_clients.update({slow, fast})
        sending = asyncio.create_task(pipeline.broadcast({"type": "test"}))
        await asyncio.wait_for(delivered.wait(), timeout=0.2)
        await asyncio.wait_for(sending, timeout=1.5)
        self.assertNotIn(slow, pipeline.ws_clients)
        self.assertIn(fast, pipeline.ws_clients)


class ControllerAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Config()
        config.llm.provider = "mock"
        config.tts.engine = "none"
        config.reply_interval.enabled = False
        config.danmaku_filter.time_window = 0.02
        config.idle.enabled = False
        cls.config_patch = patch("src.server.Config.load", return_value=config)
        cls.config_patch.start()
        cls.settings_temp = tempfile.TemporaryDirectory()
        cls.settings_patch = patch("src.server.settings_path", return_value=Path(cls.settings_temp.name) / "settings.json")
        cls.settings_patch.start()
        cls.stdin_patch = patch("src.danmaku.sys.stdin.isatty", return_value=False)
        cls.stdin_patch.start()
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.config_patch.stop()
        cls.settings_patch.stop()
        cls.stdin_patch.stop()
        cls.settings_temp.cleanup()

    def test_invalid_payloads_are_422_not_server_errors(self):
        for payload in [
            {}, {"content": " "}, {"content": "x"}, {"content": "a" * 51},
            {"content": "你好", "user_id": "bad"}, {"content": ["bad"]},
            {"content": "你好", "priority": -1}, {"content": "你好", "msg_type": "unknown"},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/danmaku", json=payload).status_code, 422)
        self.assertEqual(self.client.post("/api/test-event", json={"type": "unknown"}).status_code, 422)
        self.assertEqual(self.client.post("/api/test-event", json={"type": "action_test", "action": "unknown"}).status_code, 422)

    def test_new_socket_gets_snapshot_then_chat_and_reply(self):
        with self.client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            self.assertEqual(initial["type"], "status")
            response = self.client.post("/api/danmaku", json={"content": "你好呀", "user_name": "测试观众"})
            self.assertEqual(response.status_code, 200)
            events = []
            for _ in range(12):
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "response":
                    break
            self.assertTrue(any(event["type"] == "danmaku" and event["user_name"] == "测试观众" for event in events))
            self.assertTrue(any(event["type"] == "response" for event in events))

    def test_config_is_public_and_assets_are_served(self):
        config = self.client.get("/api/config").json()
        self.assertNotIn("api_key", config)
        from src.studio import ACTION_NAMES
        self.assertEqual(set(config["actions"]), set(ACTION_NAMES))
        for path in ["/control", "/", "/rig-preview", "/static/control.js", "/static/cutout-rig.js",
                     "/static/avatar_dragon/rig.json", "/static/avatar_dragon/rig-body.png",
                     "/static/avatar_dragon/rig-parts.png", "/static/avatar_dragon/neutral.png"]:
            self.assertEqual(self.client.get(path).status_code, 200)
        status = self.client.get("/api/status").json()
        self.assertTrue(status["healthy"])
        self.assertIn("queue_capacity", status)


if __name__ == "__main__":
    unittest.main()
