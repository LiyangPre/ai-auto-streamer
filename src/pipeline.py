"""Pipeline orchestrator: coordinates danmaku → router → LLM → TTS → action"""
import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from .config import Config
from .danmaku import DanmakuMessage, BilibiliDanmakuCollector, TextInputCollector, MessageQueue
from .llm import CallParams, LLMError, LLMProcessor, MockLLM
from .memory import ShortTermMemory
from .router import GIFT, IntentRouter
from .tts import TTSEngine
from .action import ActionMapper
from .safety import SafetyGate
from .speech_text import chat_readback
from .stage_activity import StageActivity
from .welcome import AudienceWelcomes

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: Config):
        self.config = config
        self.action_mapper = ActionMapper(config.action)
        self.danmaku_queue = MessageQueue(config.danmaku_filter.ingress_max_size)
        self.result_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self.ws_clients: set = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._background_tasks: set[asyncio.Task] = set()

        self.llm: Optional[LLMProcessor] = None
        self.tts: Optional[TTSEngine] = None
        self.collector = None
        self.memory = ShortTermMemory(config.memory)
        self.router = IntentRouter(config.router)
        self.safety = SafetyGate(config.safety)

        self._last_reply_time: float = 0.0
        self._reply_lock = asyncio.Lock()
        self.stage = StageActivity()
        self.performance_idle = self.stage.idle
        self.ingress_handlers=[]
        self.performance_resolver=lambda owner:None
        self.welcomes=AudienceWelcomes(self)
        self.danmaku_queue.interceptor=self._intercept_ingress
        self._last_activity_time = time.time()
        self._last_viewer_time = self._last_activity_time
        self._last_performance_end_time = self._last_activity_time
        self._last_idle_time = self._last_activity_time
        self._last_warmup_time = 0.0
        self._warmup_task = None
        self._warmup_bag = []
        self._last_warmup_line = None
        self._reply_started = 0.0
        self._window_dropped = 0
        self.metrics = {"responses": 0, "templates": 0, "llm_calls": 0, "last_reply_ms": None,
                        "first_text_ms": None, "first_audio_ms": None, "warmups": 0}
        self._metrics_generation = 0
        self._metrics_started = 0.0

    @property
    def performance_active(self):
        return self.stage.owner is not None

    def _intercept_ingress(self,message):
        self._last_viewer_time=time.time()
        if self.welcomes.offer(message):return True
        return any(handler(message) for handler in tuple(self.ingress_handlers))

    @property
    def _is_replying(self) -> bool:
        return self._reply_lock.locked()

    @property
    def healthy(self) -> bool:
        if not self._running or any(task.done() for task in self._tasks):
            return False
        return not isinstance(self.collector, BilibiliDanmakuCollector) or self.collector.connected

    async def start(self):
        logger.info("Starting pipeline...")
        provider = self.config.llm.provider
        if provider == "mock":
            self.llm = MockLLM(self.config.character)
        elif provider:
            self.llm = LLMProcessor(self.config.llm, self.config.character)

        self.tts = None if self.config.tts.engine == "none" else TTSEngine(self.config.tts)
        if self.config.platform.type == "bilibili":
            self.collector = BilibiliDanmakuCollector(self.config.platform, self.danmaku_queue)
        else:
            self.collector = TextInputCollector(self.danmaku_queue)

        self._running = True
        self._last_reply_time = 0.0
        self._tasks = [
            asyncio.create_task(self.collector.start()),
            asyncio.create_task(self._process_loop()),
            asyncio.create_task(self._cleanup_loop()),
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._broadcast_loop()),
            asyncio.create_task(self.welcomes.run()),
        ]
        logger.info("Pipeline started successfully")

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)

        def finished(completed):
            self._background_tasks.discard(completed)
            if not completed.cancelled() and completed.exception():
                logger.error(f"Background reply error: {completed.exception()}", exc_info=True)

        task.add_done_callback(finished)
        return task

    async def _emit(self, **payload):
        payload.setdefault("timestamp", time.time())
        if payload.get("type") in {"response_start", "response"} and self._metrics_started and self.metrics["first_text_ms"] is None:
            self.metrics["first_text_ms"] = round((time.monotonic() - self._metrics_started) * 1000)
        if payload.get("type") in {"response", "response_end"} and not payload.get("blocked"):
            self.metrics["responses"] += 1
        await self.result_queue.put(payload)

    def status(self):
        now = time.time()
        return {
            "type": "status", "timestamp": now, "healthy": self.healthy,
            "queue_count": self.danmaku_queue.qsize(),
            "queue_capacity": self.danmaku_queue.maxsize,
            "dropped": self.danmaku_queue.dropped + self._window_dropped,
            "cooldown_remaining": round(max(0, self.config.reply_interval.cooldown - (now - self._last_reply_time)), 1)
                if self.config.reply_interval.enabled else 0,
            "idle_seconds": round(max(0, now - self._last_activity_time)),
            "replying": self._is_replying,
            "singing": self.stage.owner == 'singing',
            "active_module": self.stage.owner or 'chat',
            **self.metrics,
        }

    async def _process_loop(self):
        while self._running:
            try:
                await self.performance_idle.wait()
                if self.config.danmaku_filter.enabled:
                    await self._process_with_filter()
                else:
                    msg = await asyncio.wait_for(self.danmaku_queue.get(), timeout=1.0)
                    self.danmaku_queue.task_done()
                    await self._handle_window([msg])
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Pipeline error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _process_with_filter(self):
        collected: List[DanmakuMessage] = []
        start_time = time.monotonic()
        time_window = self.config.danmaku_filter.time_window

        while time.monotonic() - start_time < time_window:
            try:
                msg = await asyncio.wait_for(self.danmaku_queue.get(), timeout=0.1)
                self.danmaku_queue.task_done()
                collected.append(msg)
                self._last_activity_time = time.time()
                # Bound the aggregation window as well as the ingress queue.
                if len(collected) > self.config.danmaku_filter.max_queue_size:
                    collected = sorted(collected, key=lambda m: (m.priority, m.timestamp))[-self.config.danmaku_filter.max_queue_size:]
                    self._window_dropped += 1
            except asyncio.TimeoutError:
                continue

        if not collected:
            return

        await self._emit(type="queue_update", count=len(collected))
        await self._handle_window(collected)

    async def _handle_window(self, collected: List[DanmakuMessage]):
        await self.performance_idle.wait()
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
            await asyncio.gather(self._warmup_task, return_exceptions=True)
        self._last_activity_time = time.time()
        for message in collected:
            if self.safety.allows_input(message.content):
                await self._emit(type="danmaku", user_name=message.user_name,
                                 content=message.content, msg_type=message.msg_type)
        gifts = [m for m in collected if m.msg_type == "gift"]
        enters = [m for m in collected if m.msg_type == "enter"]
        speeches = self._select_speech(collected)

        for msg in gifts:
            await self._handle_gift(msg)
        for msg in enters:
            await self._handle_enter(msg)
        if speeches:
            await self._handle_speech(speeches)

    def _select_speech(self, messages: List[DanmakuMessage]) -> List[DanmakuMessage]:
        cfg = self.config.danmaku_filter
        valid = [
            m for m in messages
            if m.msg_type == "danmaku"
            and cfg.min_length <= len(m.content) <= cfg.max_length
            and self.safety.allows_input(m.content)
        ]
        latest_per_user: dict = {}
        for message in sorted(valid, key=lambda item: item.timestamp):
            latest_per_user[message.user_id] = message

        deduped = sorted(latest_per_user.values(), key=lambda item: item.timestamp)
        return deduped[-cfg.batch_max:]

    async def _handle_gift(self, msg: DanmakuMessage):
        route = self.router.route_for(GIFT)
        if not route.enabled or not self.safety.allows_input(msg.content):
            return

        self.memory.register_gift(
            msg.user_id,
            msg.user_name,
            msg.gift_name or "礼物",
            msg.gift_num or 1,
            msg.timestamp,
            price=msg.price,
            priority=msg.priority,
        )
        if msg.priority >= 2:
            pending = self.memory.pop_gift(msg.user_id, msg.timestamp)
            if pending:
                self._spawn(self._thank_gift(msg.user_id, pending))

    async def _thank_gift(self, user_id: int, pending):
        route = self.router.route_for(GIFT)
        if not route.enabled:
            return

        description = pending.describe()
        self.memory.add(user_id, pending.user_name, f"送出 {description}", kind="gift")
        prompt = f"{pending.user_name} 送出了 {description}"
        if pending.price:
            prompt += f"，累计价值 {pending.price:g} 元"
        params = CallParams(
            temperature=route.temperature,
            max_tokens=route.max_tokens,
            instruction=route.instruction,
            context=self.memory.render(user_id, pending.user_name),
            user_name=pending.user_name,
        )
        await self._reply(prompt, params, stream=False, priority=pending.priority)

    async def _handle_enter(self, msg: DanmakuMessage):
        route = self.router.route(msg).route
        if not route.enabled or not self.memory.should_welcome_enter(msg.user_id, msg.timestamp):
            return

        self.memory.add(msg.user_id, msg.user_name, "进入直播间", kind="enter")
        returning = self.memory.is_returning(msg.user_id)
        text = None
        if route.use_template and route.templates:
            template = route.templates[1] if returning and len(route.templates) > 1 else route.templates[0]
            text = self.router.fill_template(template, user_name=msg.user_name)

        if text:
            await self._speak_direct(text, route.emotion)
            return

        prompt = f"{msg.user_name} 进入了直播间" + ("（是老观众）" if returning else "（第一次来）")
        params = CallParams(
            temperature=route.temperature,
            max_tokens=route.max_tokens,
            instruction=route.instruction or "简短欢迎这位观众进直播间，一句话即可。",
            user_name=msg.user_name,
        )
        await self._reply(prompt, params, stream=False)

    async def _handle_speech(self, messages: List[DanmakuMessage]):
        speech_prefix = chat_readback(messages) if self.config.character.read_chat else ""
        if speech_prefix and not self.safety.allows_output(speech_prefix):
            speech_prefix = ""
        if len(messages) == 1:
            msg = messages[0]
            self.memory.add(msg.user_id, msg.user_name, msg.content, timestamp=msg.timestamp)
            decision = self.router.route(msg)
            if not decision.route.enabled:
                return
            if decision.is_template:
                await self._speak_direct(decision.template_text, decision.route.emotion, speech_prefix=speech_prefix)
                return

            params = CallParams(
                temperature=decision.route.temperature,
                max_tokens=decision.route.max_tokens,
                instruction=decision.route.instruction,
                context=self.memory.render(msg.user_id, msg.user_name),
                user_name=msg.user_name,
            )
            await self._reply(msg.content, params, stream=self.config.llm.stream, speech_prefix=speech_prefix)
            return

        decision = self.router.route_batch(messages)
        if not decision.route.enabled:
            return
        for msg in messages:
            self.memory.add(msg.user_id, msg.user_name, msg.content, timestamp=msg.timestamp)

        names = [m.user_name for m in messages]
        combined = "\n".join(f"{m.user_name}: {m.content}" for m in messages)
        instruction = (decision.route.instruction + " " if decision.route.instruction else "") + (
            f"刚才有 {len(messages)} 位观众同时发言（{('、'.join(names))}），"
            "请在一次回复里自然地点到其中至少两位的昵称，不要逐条回答。"
        )
        max_tokens = max(decision.route.max_tokens or 128, 48 * len(messages))
        params = CallParams(
            temperature=decision.route.temperature,
            max_tokens=max_tokens,
            instruction=instruction,
            context=self.memory.render(messages[-1].user_id, messages[-1].user_name),
        )
        await self._reply(combined, params, stream=self.config.llm.stream, speech_prefix=speech_prefix)

    @asynccontextmanager
    async def _reply_slot(self, priority: int = 0):
        cfg = self.config.reply_interval
        max_wait = cfg.paid_max_wait if priority else cfg.max_wait
        while True:
            await self.performance_idle.wait()
            try:
                await asyncio.wait_for(self._reply_lock.acquire(), timeout=max_wait)
            except asyncio.TimeoutError:
                await self._emit(type="reply_dropped", reason="reply_slot_timeout", priority=priority)
                yield False
                return
            if not self.performance_active:
                break
            self._reply_lock.release()

        previous_responses = self.metrics["responses"]
        try:
            if cfg.enabled:
                wait_time = cfg.cooldown - (time.time() - self._last_reply_time)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            self._reply_started = time.monotonic()
            self._metrics_started = self._reply_started
            self._metrics_generation += 1
            self.metrics["first_text_ms"] = self.metrics["first_audio_ms"] = None
            yield True
        finally:
            if self._reply_started and self.metrics["responses"] > previous_responses:
                self.metrics["last_reply_ms"] = round((time.monotonic() - self._reply_started) * 1000)
            self._last_reply_time = time.time()
            self._last_activity_time = self._last_reply_time
            self._reply_started = 0.0
            self._reply_lock.release()

    async def _speak_direct(self, text: str, emotion: str, source="template", speech_prefix=""):
        text = (text or "").strip()
        if not text or not self.safety.allows_output(text):
            return

        async with self._reply_slot() as acquired:
            if not acquired:
                return
            if source == "template":
                self.metrics["templates"] += 1
            await self._emit(
                type="response",
                text=text,
                emotion=emotion,
                audio_url=None,
                action=self.action_mapper.get_action(emotion),
            )
            self.memory.remember_reply(text)
            await self._emit_audio(speech_prefix + text, emotion)

    async def _reply(self, message: str, params: CallParams, stream: bool, priority: int = 0, speech_prefix=""):
        if not self.llm:
            await self._emit(type="response_error", reason="llm_not_configured")
            return

        async with self._reply_slot(priority) as acquired:
            if not acquired:
                return
            self.metrics["llm_calls"] += 1
            await self._emit(type="thinking_start", action=self.action_mapper.get_action("thinking"))
            text = ""
            try:
                if stream and not isinstance(self.llm, MockLLM):
                    text = await self._reply_stream(message, params, speech_prefix=speech_prefix)
                else:
                    text, emotion = await self.llm.chat(message, params)
                    if not text.strip():
                        raise LLMError("LLM returned an empty reply")
                    if not self.safety.allows_output(text):
                        await self._emit(type="moderation", stage="output")
                        await self._emit(type="response_end", full_text="", emotion=emotion, blocked=True)
                        return
                    await self._emit(
                        type="response",
                        text=text,
                        emotion=emotion,
                        audio_url=None,
                        action=self.action_mapper.get_action(emotion),
                    )
                    await self._emit_audio(speech_prefix + text, emotion)
                if text:
                    self.memory.remember_reply(text)
            except LLMError as e:
                logger.warning(f"Reply skipped after LLM failure: {e}")
                await self._emit(type="response_error", reason="llm_failed", message=str(e))
            finally:
                await self._emit(type="thinking_end")
            return text

    def _take_sentence(self, text: str, final: bool = False):
        stripped = text.strip()
        if not stripped:
            return None, ""
        # Keep a spoken thought intact. A comma is an early boundary only after
        # enough text has arrived; tiny fragments reset the voice's intonation.
        match = re.search(r"[。！？!?；;\n]", text)
        if not match and self.config.tts.delivery == "fast":
            match = re.search(r"[，,、]", text[self.config.tts.min_phrase_chars:])
            if match:
                end = self.config.tts.min_phrase_chars + match.end()
                return text[:end].strip(), text[end:].lstrip()
        if match:
            end = match.end()
            return text[:end].strip(), text[end:].lstrip()
        limit = self.config.tts.max_sentence_chars
        if len(text) >= limit:
            return text[:limit].strip(), text[limit:].lstrip()
        if final:
            return stripped, ""
        return None, text

    async def _reply_stream(self, message: str, params: CallParams, speech_prefix="") -> str:
        streamed_text = ""
        pending_audio = ""
        emotion = "normal"
        first_chunk = True
        blocked = False
        audio_queue = asyncio.Queue(maxsize=4)

        async def audio_worker():
            index = 0
            while True:
                sentence = await audio_queue.get()
                if sentence is None:
                    return
                if index == 0:
                    sentence = speech_prefix + sentence
                audio_url = await self._synthesize(sentence, emotion)
                if audio_url:
                    await self._emit(
                        type="audio_segment",
                        text=sentence,
                        emotion=emotion,
                        audio_url=audio_url,
                        index=index,
                    )
                index += 1

        use_sentence_audio = self.config.tts.sentence_stream
        worker = asyncio.create_task(audio_worker()) if use_sentence_audio else None
        completed = False
        try:
            async for chunk, chunk_emotion in self.llm.chat_stream(message, params):
                if chunk_emotion:
                    emotion = chunk_emotion
                streamed_text += chunk
                if not self.safety.allows_output(streamed_text):
                    blocked = True
                    break

                if first_chunk:
                    await self._emit(
                        type="response_start",
                        emotion=emotion,
                        action=self.action_mapper.get_action(emotion),
                    )
                    first_chunk = False
                await self._emit(type="response_chunk", text=chunk)

                if use_sentence_audio:
                    pending_audio += chunk
                    while True:
                        sentence, pending_audio = self._take_sentence(pending_audio)
                        if sentence is None:
                            break
                        await audio_queue.put(sentence)

            if first_chunk and not blocked:
                raise LLMError("LLM returned an empty stream")
            if blocked:
                if worker:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                    worker = None
                await self._emit(type="moderation", stage="output")
            elif use_sentence_audio:
                sentence, pending_audio = self._take_sentence(pending_audio, final=True)
                if sentence:
                    await audio_queue.put(sentence)
                completed = True
        finally:
            if worker:
                if completed:
                    await audio_queue.put(None)
                    await worker
                else:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

        await self._emit(
            type="response_end",
            full_text="" if blocked else streamed_text,
            emotion=emotion,
            blocked=blocked,
            audio_url=None if use_sentence_audio else await self._synthesize(
                "" if blocked else speech_prefix + streamed_text, emotion
            ),
        )
        return "" if blocked else streamed_text

    async def _synthesize(self, text: str, emotion: str) -> Optional[str]:
        if not self.tts or not (text or "").strip():
            return None
        generation, started = self._metrics_generation, self._metrics_started

        def first_audio():
            if started and generation == self._metrics_generation and self.metrics["first_audio_ms"] is None:
                self.metrics["first_audio_ms"] = round((time.monotonic() - started) * 1000)

        async def failed():
            await self._emit(type="tts_error", reason="synthesis_failed")

        if self.config.tts.progressive_audio and hasattr(self.tts, "create_stream"):
            return await self.tts.create_stream(text, emotion, on_first_audio=first_audio, on_error=failed)
        audio_url = await self.tts.synthesize(text, emotion)
        if audio_url:
            first_audio()
        if not audio_url:
            await self._emit(type="tts_error", reason="synthesis_failed")
        return audio_url

    async def _emit_audio(self, text: str, emotion: str):
        audio_url = await self._synthesize(text, emotion)
        if audio_url:
            await self._emit(type="audio_segment", text=text, emotion=emotion, audio_url=audio_url, index=0)

    async def _cleanup_loop(self):
        while self._running:
            await asyncio.sleep(60)
            self.memory.prune()
            if self.tts:
                self.tts.cleanup_old_files(max_age_seconds=3600)

    async def _tick_loop(self):
        while self._running:
            await asyncio.sleep(1)
            await self._emit(**self.status())
            if self.performance_active:
                continue
            due = self.memory.flush_due_gifts(max_wait=self.config.reply_interval.paid_max_wait)
            for user_id, pending in due:
                self._spawn(self._thank_gift(user_id, pending))

            if self.config.idle.enabled and not self._is_replying and time.time() - max(self._last_activity_time, self._last_idle_time) >= self.config.idle.threshold:
                await self.trigger_idle_action()
                self._last_idle_time = time.time()
            self._maybe_warmup()

    async def trigger_action(self, name: str):
        action = self.action_mapper.get_action_by_name(name)
        await self._emit(type="idle_action", action=action, emotion=action.get("emotion", "normal"))

    async def trigger_idle_action(self):
        import random

        if self.config.idle.actions:
            await self.trigger_action(random.choice(self.config.idle.actions))

    def _maybe_warmup(self, now=None):
        now = time.time() if now is None else now
        idle = self.config.idle
        if (self.performance_active or not self._running or not idle.self_talk or not self.tts or not self.ws_clients
                or self._is_replying or self._background_tasks or not self.danmaku_queue.empty()
                or (isinstance(self.collector, BilibiliDanmakuCollector) and not self.collector.connected)
                or now - self._last_activity_time < idle.self_talk_delay
                or now - self._last_warmup_time < idle.self_talk_interval):
            return False
        self._last_warmup_time = now
        self._warmup_task = self._spawn(self._speak_warmup(self._last_activity_time))
        return True

    def _warmup_line(self):
        import random
        lines = list(dict.fromkeys(line.strip() for line in self.config.idle.self_talk_prompts
                     if 2 <= len(line.strip()) <= 120 and self.safety.allows_output(line)))
        if not lines:
            return None
        self._warmup_bag = [line for line in self._warmup_bag if line in lines]
        if not self._warmup_bag:
            self._warmup_bag = lines.copy()
            random.shuffle(self._warmup_bag)
            if len(lines) > 1 and self._warmup_bag[-1] == self._last_warmup_line:
                self._warmup_bag[0], self._warmup_bag[-1] = self._warmup_bag[-1], self._warmup_bag[0]
        return self._warmup_bag.pop()

    async def _speak_warmup(self, activity_time):
        # Low-priority speech is prepared completely before broadcasting, so a
        # new viewer message can cancel it without leaving queued stale audio.
        if self._is_replying or not self.config.idle.self_talk or activity_time != self._last_activity_time:
            return
        async with self._reply_lock:
            text = self._warmup_line()
            if not text or not self.tts:
                return
            started = time.monotonic()
            url = await self.tts.synthesize(text, "happy")
            if (not url or not self.config.idle.self_talk or activity_time != self._last_activity_time
                    or not self.danmaku_queue.empty() or not self.ws_clients):
                return
            self._metrics_generation += 1
            self._metrics_started = started
            elapsed = round((time.monotonic() - started) * 1000)
            self.metrics.update(first_text_ms=None, first_audio_ms=elapsed, last_reply_ms=elapsed)
            await self._emit(type="response", text=text, emotion="happy", source="warmup", audio_url=url,
                             action=self.action_mapper.get_action("happy"))
            self.metrics["warmups"] += 1
            self._last_warmup_line = text
            self._last_reply_time = self._last_activity_time = time.time()
            self.memory.remember_reply(text)

    async def broadcast(self, message: dict):
        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=1.0)
            except Exception as e:
                logger.warning(f"WebSocket send error: {e}")
                self.ws_clients.discard(ws)
                try:
                    await asyncio.wait_for(ws.close(code=1013), timeout=0.2)
                except Exception:
                    pass
        await asyncio.gather(*(send(ws) for ws in list(self.ws_clients)))

    async def _broadcast_loop(self):
        while self._running:
            try:
                result = await asyncio.wait_for(self.result_queue.get(), timeout=1.0)
                await self.broadcast(result)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Broadcast error: {e}")

    async def stop(self):
        logger.info("Stopping pipeline...")
        self._running = False
        if self.collector:
            await self.collector.stop()

        tasks = self._tasks + list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._background_tasks.clear()

        if isinstance(self.llm, LLMProcessor):
            await self.llm.close()
        if self.tts and hasattr(self.tts, "close"):
            await self.tts.close()
        for ws in list(self.ws_clients):
            await ws.close()
        self.ws_clients.clear()
        logger.info("Pipeline stopped")
