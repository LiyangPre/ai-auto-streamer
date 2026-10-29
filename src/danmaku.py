"""Bilibili danmaku collector using blivedm"""
import asyncio
import hashlib
import inspect
import logging
import sys
import threading
import time
import os
from http.cookies import SimpleCookie
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .config import PlatformConfig

logger = logging.getLogger(__name__)
REJECTED = object()

try:
    import blivedm
except ImportError:
    blivedm = None


@dataclass
class DanmakuMessage:
    content: str
    user_name: str
    user_id: int
    timestamp: float
    msg_type: str = "danmaku"
    gift_name: Optional[str] = None
    gift_num: int = 0
    price: float = 0.0
    priority: int = 0
    anonymous: bool = False

    def __post_init__(self):
        if not self.user_id:
            self.anonymous = True
            # Session fallback only; never mistake every redacted uid for one
            # viewer. Negative IDs cannot collide with real Bilibili accounts.
            self.user_id = -stable_user_id(self.user_name)


def stable_user_id(user_name: str) -> int:
    digest = hashlib.sha256(user_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:6], "big") + 1


class MessageQueue(asyncio.Queue):
    """Bound ingress, retaining higher-priority events and the newest chat."""

    def __init__(self, maxsize=100):
        super().__init__(maxsize=maxsize)
        self.dropped = 0
        self.interceptor = None

    def offer(self, message: DanmakuMessage) -> bool:
        if self.interceptor:
            intercepted = self.interceptor(message)
            if intercepted is REJECTED:
                self.dropped += 1
                return False
            if intercepted:
                return True
        if not self.full():
            super().put_nowait(message)
            return True
        buffered = []
        while not self.empty():
            buffered.append(super().get_nowait())
            self.task_done()
        candidates = buffered + [message]
        discard = min(range(len(candidates)), key=lambda i: candidates[i].priority)
        for index, item in enumerate(candidates):
            if index != discard:
                super().put_nowait(item)
        self.dropped += 1
        return discard != len(candidates) - 1

    def put_nowait(self, item):
        self.offer(item)

    async def put(self, item):
        self.offer(item)


class BilibiliDanmakuCollector:
    def __init__(self, config: PlatformConfig, message_queue: asyncio.Queue):
        self.config = config
        self.queue = message_queue
        self._session: Optional[aiohttp.ClientSession] = None
        self._client = None
        self._running = False
        self.connected = False

    async def start(self):
        if blivedm is None:
            logger.error("Bilibili client missing. Activate .venv and install requirements.txt")
            return
        if inspect.iscoroutinefunction(blivedm.BaseHandler.handle):
            logger.error("Unsupported PyPI blivedm. Activate .venv and install the pinned requirements.txt dependency")
            return
        if self.config.room_id <= 0:
            logger.error("Set platform.room_id to a positive Bilibili room ID before connecting")
            return

        self._running = True
        retry_delay = 1.0
        while self._running:
            try:
                self._session = aiohttp.ClientSession()
                sessdata = os.environ.get(self.config.sessdata_env, '').strip()
                if sessdata:
                    cookie = SimpleCookie()
                    cookie['SESSDATA'] = sessdata
                    cookie['SESSDATA']['domain'] = '.bilibili.com'
                    cookie['SESSDATA']['path'] = '/'
                    self._session.cookie_jar.update_cookies(cookie)
                collector = self

                class MonitoredClient(blivedm.BLiveClient):
                    async def _parse_business_message(self, header, body):
                        # Opening a socket/sending auth does not prove authentication.
                        # The SDK raises on a rejected AUTH_REPLY (operation 8).
                        await super()._parse_business_message(header, body)
                        nonlocal retry_delay
                        if header.operation == 8:
                            collector.connected = True
                            retry_delay = 1.0

                    async def _on_ws_close(self):
                        collector.connected = False
                        await super()._on_ws_close()

                self._client = MonitoredClient(self.config.room_id, session=self._session)
                self._client.set_handler(self._make_handler())
                logger.info(f"Connecting to Bilibili room {self.config.room_id}...")
                self._client.start()
                await self._client.join()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error('Danmaku collector error (%s)', type(e).__name__)
            finally:
                self.connected = False
                if self._client:
                    try:
                        await self._client.stop_and_close()
                    except Exception:
                        logger.warning('Bilibili client cleanup failed; reconnecting')
                if self._session and not self._session.closed:
                    await self._session.close()
                self._client = None
                self._session = None

            if self._running:
                logger.info(f"Reconnecting Bilibili collector in {retry_delay:.0f}s")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    def _make_handler(self):
        queue = self.queue

        class Handler(blivedm.BaseHandler):
            def handle(self, client, command):
                cmd = command.get("cmd", "").split(":", 1)[0]
                if cmd == "INTERACT_WORD":
                    data = command.get("data", {})
                    # 2=follow, 3=share; these are not room-entry events.
                    if data.get("msg_type", 1) == 1:
                        self._enter(data.get("uname", "观众"), data.get("uid", 0))
                    return
                super().handle(client, command)

            def _enter(self, name, uid):
                queue.put_nowait(DanmakuMessage(content="进入直播间",user_name=name,
                    user_id=uid,timestamp=time.time(),msg_type="enter"))

            def _on_interact_word_v2(self, client, message):
                if message.msg_type == 1:
                    self._enter(message.username, message.uid)

            def _on_danmaku(self, client, message):
                queue.put_nowait(DanmakuMessage(
                    content=message.msg,
                    user_name=message.uname,
                    user_id=message.uid,
                    timestamp=time.time(),
                ))
                logger.info('Received Bilibili chat (uid=%s)', message.uid)

            def _on_gift(self, client, message):
                price = (message.total_coin or 0) / 1000 if message.coin_type == "gold" else 0.0
                queue.put_nowait(DanmakuMessage(
                    content=f"赠送了 {message.gift_name} x{message.num}",
                    user_name=message.uname,
                    user_id=message.uid,
                    timestamp=time.time(),
                    msg_type="gift",
                    gift_name=message.gift_name,
                    gift_num=message.num,
                    price=price,
                    priority=1 if price > 0 else 0,
                ))
                logger.info('Received Bilibili gift (uid=%s, quantity=%s)', message.uid, message.num)

            def _on_super_chat(self, client, message):
                content = message.message or ''
                queue.put_nowait(DanmakuMessage(
                    content=content,
                    user_name=message.uname,
                    user_id=message.uid,
                    timestamp=time.time(),
                    msg_type="super_chat",
                    gift_name="醒目留言",
                    gift_num=1,
                    price=float(message.price or 0),
                    priority=4,
                ))

            def _on_buy_guard(self, client, message):
                queue.put_nowait(DanmakuMessage(
                    content=f"开通了 {message.gift_name} x{message.num}",
                    user_name=message.username,
                    user_id=message.uid,
                    timestamp=time.time(),
                    msg_type="gift",
                    gift_name=message.gift_name,
                    gift_num=message.num,
                    price=(message.price or 0) * message.num / 1000,
                    priority=3,
                ))

        return Handler()

    async def stop(self):
        self._running = False
        self.connected = False
        if self._client:
            self._client.stop()
        # start() owns ordered client and session cleanup in its finally block.


class TextInputCollector:
    def __init__(self, message_queue: asyncio.Queue):
        self.queue = message_queue
        self._running = False
        self._user_name = "测试用户"
        self._user_id = stable_user_id(self._user_name)

    async def start(self):
        self._running = True
        if not sys.stdin.isatty():
            logger.info("Text collector is using /api/danmaku (stdin is not interactive)")
            while self._running:
                await asyncio.sleep(1)
            return

        print("\n" + "=" * 50)
        print("文本输入模式 (输入 'quit' 退出)")
        print("切换模拟用户: /name 昵称")
        print("=" * 50 + "\n")

        input_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def read_console():
            while self._running:
                try:
                    text = input(f"{self._user_name}: ")
                    future = asyncio.run_coroutine_threadsafe(input_queue.put(text), loop)
                    future.result()
                except (EOFError, OSError, RuntimeError):
                    break

        threading.Thread(target=read_console, daemon=True).start()
        while self._running:
            try:
                text = await asyncio.wait_for(input_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if text.lower() == "quit":
                break
            if text.startswith("/name "):
                self._user_name = text[6:].strip() or "测试用户"
                self._user_id = stable_user_id(self._user_name)
                logger.info(f"模拟用户切换为: {self._user_name} (uid={self._user_id})")
                continue
            if text.strip():
                await self.queue.put(DanmakuMessage(
                    content=text.strip(),
                    user_name=self._user_name,
                    user_id=self._user_id,
                    timestamp=time.time(),
                ))
                logger.info(f"[{self._user_name}]: {text}")

    async def stop(self):
        self._running = False
