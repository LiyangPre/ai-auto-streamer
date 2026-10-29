"""Bounded paid inbox; one consumer, gift combos and SC-before-gift priority."""
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from .danmaku import DanmakuMessage


@dataclass
class PaidItem:
    message: DanmakuMessage
    first_at: float
    last_at: float
    gifts: dict = field(default_factory=dict)


class PaidInbox:
    def __init__(self, capacity=128, window=4):
        if capacity < 1 or window < 0:
            raise ValueError('Paid inbox capacity must be positive and window non-negative')
        self.capacity = capacity
        self.window = window
        self.items = OrderedDict()
        self.sequence = 0
        self.overflow = 0

    def offer(self, message):
        if message.msg_type not in {'gift', 'super_chat'}:
            raise ValueError('Only gifts and super chats belong in the paid inbox')
        now = time.monotonic()
        self.sequence += 1
        sc = message.msg_type == 'super_chat'
        # Own the queued record: combo updates must never mutate an ingress
        # event already logged, inspected or retained by another consumer.
        message = replace(message, priority=4 if sc else min(3, max(0, message.priority)))
        key = ('sc', self.sequence) if sc else ('gift', message.user_id)
        item = self.items.get(key)
        if item:
            item.last_at = now
            item.message.price += message.price
            item.message.priority = max(item.message.priority, message.priority)
            item.message.user_name = message.user_name
            name = message.gift_name or '礼物'
            if name not in item.gifts and len(item.gifts) >= 15:
                name = '其他礼物'
            item.gifts[name] = item.gifts.get(name, 0) + max(1, message.gift_num)
            return True
        if len(self.items) >= self.capacity:
            # SC takes precedence over gifts, without unbounded waiting tasks.
            lowest = min(self.items, key=lambda k: self.items[k].message.priority)
            self.overflow += 1
            if self.items[lowest].message.priority >= message.priority:
                return False
            del self.items[lowest]
        self.items[key] = PaidItem(message, now, now,
                                  {} if sc else {message.gift_name or '礼物': max(1, message.gift_num)})
        return True

    def take(self, limit=8, now=None):
        if limit < 1:
            return []
        now = time.monotonic() if now is None else now
        ordered = sorted(self.items, key=lambda k: (-self.items[k].message.priority, self.items[k].first_at))
        ready = [k for k in ordered if self.items[k].message.priority >= 2
                 or now - self.items[k].last_at >= self.window
                 or now - self.items[k].first_at >= 20]
        if not ready:
            return []
        if self.items[ready[0]].message.msg_type == 'super_chat':
            return [self.items.pop(ready[0])]
        return [self.items.pop(k) for k in ready[:limit] if self.items[k].message.msg_type != 'super_chat']
