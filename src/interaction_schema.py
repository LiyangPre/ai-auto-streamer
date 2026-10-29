"""Strict interaction envelopes shared by provider fallback and stage dispatch.

This module has no model client dependency. Validation must happen before a
provider attempt succeeds, then again with the exact audience/purpose limits.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audience_intents import IntentDecision

_LEADING_EMOTION = re.compile(r'^\[(?:happy|sad|angry|surprised|thinking|normal)\]\s*', re.I)
_JSON_FENCE = re.compile(r'```json[ \t]*\r?\n(.*?)\r?\n```', re.I | re.S)


class IndexedIntent(IntentDecision):
    index: int = Field(ge=0, le=15, strict=True)


class InteractionReply(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reply: str = Field(min_length=1, max_length=240)
    emotion: Literal['happy', 'sad', 'angry', 'surprised', 'thinking', 'normal'] = 'normal'
    intents: list[IndexedIntent] = Field(default_factory=list, max_length=16)

    @field_validator('reply', mode='before')
    @classmethod
    def remove_legacy_emotion_prefix(cls, value):
        # The ordinary chat system prompt uses [happy]. Some providers retain
        # it inside the structured reply too. Strip that one known prefix only;
        # do not rewrite metadata, unknown brackets or tags inside the sentence.
        if isinstance(value, str):
            stripped = value.lstrip()
            match = _LEADING_EMOTION.match(stripped)
            if match:
                return stripped[match.end():].strip()
        return value


def parse_interaction(text, message_count, max_chars=120, *, require_all=False):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError('Invalid interaction envelope')
    text = text.strip()
    fence = _JSON_FENCE.fullmatch(text)
    if fence:
        text = fence.group(1)
    data = InteractionReply.model_validate_json(text)
    if (not data.reply.strip() or len(data.reply) > max_chars
            or any(c in data.reply for c in ('{', '}', '<', '>'))):
        raise ValueError('Spoken response exceeds its contract')
    indices = [item.index for item in data.intents]
    if len(set(indices)) != len(indices) or any(i >= message_count for i in indices):
        raise ValueError('Untrusted message index')
    if require_all and set(indices) != set(range(message_count)):
        raise ValueError('Missing audience classifications')
    return data
