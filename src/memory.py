"""短期记忆：全局滚动窗口 + per-user 记忆

主键用 user_id 而非 user_name —— B站昵称可改可重复，uid 才稳定。
双层结构的原因：只按用户分组会丢掉直播间整体氛围（接梗需要知道别人刚说了什么）。
"""
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .config import MemoryConfig

logger = logging.getLogger(__name__)


@dataclass
class MemoryEvent:
    user_id: int
    user_name: str
    content: str
    timestamp: float
    kind: str = "danmaku"  # danmaku | gift | enter | reply


@dataclass
class PendingGift:
    """礼物连击缓冲区：窗口内同一观众的礼物按名称累加，结算时一次性感谢"""
    user_name: str = ""
    items: Dict[str, int] = field(default_factory=dict)
    first_at: float = 0.0
    last_at: float = 0.0
    price: float = 0.0
    priority: int = 0

    def describe(self) -> str:
        return "、".join(f"{name} x{num}" for name, num in self.items.items())


def relative_time(ts: float, now: Optional[float] = None) -> str:
    """LLM 对绝对时间戳无感，转成相对表述才有用"""
    delta = (now if now is not None else time.time()) - ts
    if delta < 10:
        return "刚刚"
    if delta < 60:
        return f"{int(delta)}秒前"
    if delta < 3600:
        return f"{int(delta // 60)}分钟前"
    return f"{int(delta // 3600)}小时前"


class ShortTermMemory:
    def __init__(self, config: MemoryConfig):
        self.config = config
        self._global: Deque[MemoryEvent] = deque(maxlen=config.global_max)
        self._by_user: Dict[int, Deque[MemoryEvent]] = defaultdict(
            lambda: deque(maxlen=config.per_user_max)
        )
        self._pending_gifts: Dict[int, PendingGift] = {}
        self._last_gift_thanks: Dict[int, float] = {}
        self._last_enter_welcome: Dict[int, float] = {}

    def add(
        self,
        user_id: int,
        user_name: str,
        content: str,
        kind: str = "danmaku",
        timestamp: Optional[float] = None,
    ) -> None:
        if not self.config.enabled or not content.strip():
            return

        event = MemoryEvent(
            user_id=user_id,
            user_name=user_name,
            content=content.strip(),
            timestamp=timestamp if timestamp is not None else time.time(),
            kind=kind,
        )
        self._global.append(event)
        # 主播自己的回复不进 per-user，避免占满某个观众的额度
        if kind != "reply":
            self._by_user[user_id].append(event)

    def remember_reply(self, text: str, timestamp: Optional[float] = None) -> None:
        """记住主播自己说过的话，用于避免重复和保持连贯"""
        self.add(-1, "主播", text, kind="reply", timestamp=timestamp)

    def clear_character_replies(self):
        """Keep viewer context and pending gifts, discard the previous persona's lines."""
        self._global = deque((event for event in self._global if event.kind != "reply"), maxlen=self.config.global_max)

    def _evict_expired(self, now: float) -> None:
        ttl = self.config.ttl_seconds
        self._global = deque(
            (event for event in self._global if now - event.timestamp <= ttl),
            maxlen=self.config.global_max,
        )

        for uid in list(self._by_user.keys()):
            dq = self._by_user[uid]
            fresh = deque(
                (event for event in dq if now - event.timestamp <= ttl),
                maxlen=self.config.per_user_max,
            )
            if fresh:
                self._by_user[uid] = fresh
            else:
                del self._by_user[uid]

        gift_retention = max(ttl, self.config.gift_thanks_cooldown)
        enter_retention = max(ttl, self.config.enter_welcome_cooldown)
        self._last_gift_thanks = {
            uid: at for uid, at in self._last_gift_thanks.items()
            if now - at <= gift_retention
        }
        self._last_enter_welcome = {
            uid: at for uid, at in self._last_enter_welcome.items()
            if now - at <= enter_retention
        }

    def user_history(self, user_id: int) -> List[MemoryEvent]:
        return list(self._by_user.get(user_id, ()))

    def prune(self) -> None:
        """Expire memory even during template-only or quiet sessions."""
        self._evict_expired(time.time())

    def render(self, user_id: Optional[int], user_name: str = "") -> str:
        """生成注入 prompt 的记忆上下文块"""
        if not self.config.enabled:
            return ""

        now = time.time()
        self._evict_expired(now)

        parts: List[str] = []

        mine = self._by_user.get(user_id) if user_id is not None else None
        if mine:
            # 事件通常按到达顺序入队，但补录/重放会打乱，渲染前统一按时间排序
            ordered = sorted(mine, key=lambda e: e.timestamp)
            lines = [
                f"  - {relative_time(e.timestamp, now)}[{e.kind}] {e.content}"
                for e in ordered
            ]
            parts.append(f"【你正在回复的观众】{user_name}\n" + "\n".join(lines))

        others = [e for e in self._global if e.user_id != user_id]
        if others:
            # 倒序展示，最近的在最后，符合 LLM 对近因的注意力偏好
            lines = [
                f"  - {e.user_name} {relative_time(e.timestamp, now)}: {e.content}"
                for e in sorted(others, key=lambda e: e.timestamp)
            ]
            parts.append("【直播间近况（其他观众与你的发言）】\n" + "\n".join(lines))

        if not parts:
            return ""

        return "\n".join(parts)

    # ---------- 礼物 ----------

    def register_gift(
        self,
        user_id: int,
        user_name: str,
        gift_name: str,
        num: int,
        now: Optional[float] = None,
        price: float = 0.0,
        priority: int = 0,
    ) -> None:
        """礼物入缓冲，不立即感谢。

        连击窗口内累加，窗口结束后由 flush_due_gifts 带累计总量谢一次。
        这样既避免同一次连击被谢两遍，也保证主播正忙时礼物不会丢。
        """
        now = now if now is not None else time.time()
        pending = self._pending_gifts.get(user_id)
        if pending is None:
            pending = PendingGift(user_name=user_name, first_at=now, last_at=now)
            self._pending_gifts[user_id] = pending

        pending.user_name = user_name
        pending.items[gift_name] = pending.items.get(gift_name, 0) + num
        pending.last_at = now
        pending.price += price
        pending.priority = max(pending.priority, priority)
        logger.debug(f"礼物入缓冲: uid={user_id} {pending.describe()}")

    def flush_due_gifts(
        self, now: Optional[float] = None, max_wait: Optional[float] = None
    ) -> List[Tuple[int, PendingGift]]:
        """结算连击窗口已结束、且感谢冷却已过的礼物缓冲。

        冷却未过时保留缓冲，等冷却结束再带着累计总量一次性感谢。
        由 pipeline 的秒级心跳循环调用。
        """
        now = now if now is not None else time.time()
        cooldown = self.config.gift_thanks_cooldown
        results: List[Tuple[int, PendingGift]] = []

        for uid in list(self._pending_gifts.keys()):
            pending = self._pending_gifts[uid]
            overdue = max_wait is not None and now - pending.first_at >= max_wait
            if not overdue and now - pending.last_at <= self.config.gift_combo_window:
                continue
            if not overdue and now - self._last_gift_thanks.get(uid, 0.0) < cooldown:
                continue
            del self._pending_gifts[uid]
            self._last_gift_thanks[uid] = now
            results.append((uid, pending))

        return results

    def pop_gift(self, user_id: int, now: Optional[float] = None) -> Optional[PendingGift]:
        pending = self._pending_gifts.pop(user_id, None)
        if pending is not None:
            self._last_gift_thanks[user_id] = now if now is not None else time.time()
        return pending

    def snapshot(self) -> dict:
        self._evict_expired(time.time())
        return {
            "global": [
                {"user": e.user_name, "kind": e.kind, "content": e.content, "at": e.timestamp}
                for e in self._global
            ],
            "users": {
                str(uid): [{"content": e.content, "kind": e.kind, "at": e.timestamp} for e in dq]
                for uid, dq in self._by_user.items()
            },
            "pending_gifts": {
                str(uid): {"user": p.user_name, "items": p.items}
                for uid, p in self._pending_gifts.items()
            },
        }

    # ---------- 进场 ----------

    def should_welcome_enter(self, user_id: int, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        last = self._last_enter_welcome.get(user_id, 0.0)
        if now - last < self.config.enter_welcome_cooldown:
            return False
        self._last_enter_welcome[user_id] = now
        return True

    def is_returning(self, user_id: int) -> bool:
        """该观众之前是否**说过话**。

        只数 danmaku：gift/enter 事件也写进 _by_user，
        若一并计入，刚进场的新观众会被自己那条 enter 判成老观众。
        """
        return any(e.kind == "danmaku" for e in self._by_user.get(user_id, ()))
