"""Bounded intent voting, independent of model calls, speech and playback.

The model may suggest an intent; only this local policy can turn repeated
positive requests into a pending action. A singing vote never plays a title:
the caller first asks the audience what to sing and resolves their answer
against the character's completed, unblocked catalog.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import math
import re
import time
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_PROFILES = 24
MAX_VOTES = 128
MAX_HISTORY = 512
INTENTS = ('singing', 'story')


class IntentDecision(BaseModel):
    """Untrusted structured model output; never an executable command."""

    model_config = ConfigDict(extra='forbid', strict=True)

    intent: Literal['none', 'singing', 'story'] = 'none'
    confidence: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    title: str | None = Field(default=None, max_length=80)
    positive: bool = True
    current_request: bool = True

    @field_validator('title')
    @classmethod
    def clean_title(cls, value):
        if value is None:
            return None
        value = unicodedata.normalize('NFKC', value).strip()
        if not value or len(value) > 80 or any(unicodedata.category(c)[0] == 'C' for c in value):
            raise ValueError('Invalid requested song title')
        return value

    @model_validator(mode='after')
    def title_belongs_to_singing(self):
        if self.title is not None and self.intent != 'singing':
            raise ValueError('Only singing intent may include a title')
        return self


class AudienceIntentPolicy(BaseModel):
    """Save under a character's module_options['interaction'] if desired."""

    model_config = ConfigDict(extra='forbid', strict=True)

    enabled: bool = True
    narrate_scene_changes: bool = True
    threshold: int = Field(default=6, ge=2, le=32)
    window_seconds: float = Field(default=120, ge=15, le=600, allow_inf_nan=False)
    trigger_cooldown: float = Field(default=300, ge=15, le=3600, allow_inf_nan=False)
    viewer_cooldown: float = Field(default=20, ge=3, le=300, allow_inf_nan=False)
    minimum_confidence: float = Field(default=.8, ge=.5, le=1, allow_inf_nan=False)
    selection_seconds: float = Field(default=90, ge=15, le=300, allow_inf_nan=False)


@dataclass
class _Vote:
    at: float
    intent: str


@dataclass
class _Profile:
    policy: AudienceIntentPolicy = field(default_factory=AudienceIntentPolicy)
    votes: deque = field(default_factory=lambda: deque(maxlen=MAX_VOTES))
    messages: OrderedDict = field(default_factory=OrderedDict)
    viewers: OrderedDict = field(default_factory=OrderedDict)
    selections: OrderedDict = field(default_factory=OrderedDict)
    last_trigger: float | None = None
    selection_started: float | None = None


class AudienceIntentPool:
    """Synchronous bounded state; callers own all async side effects.

    Call set_active_profile when the character changes. If it has never been
    called, the first recorded profile becomes active. Explicit commands bypass
    voting and remain the responsibility of the existing command handlers.
    State is deliberately not persisted: old audience votes do not survive a
    restart and trigger an unexpected performance later.
    """

    def __init__(self, *, clock=time.monotonic, policy=None):
        self._clock = clock
        self._default_policy = AudienceIntentPolicy.model_validate(policy or {})
        self._profiles = {}
        self.active_profile = None

    def _now(self):
        now = self._clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValueError('Intent clock must be finite')
        return now

    @staticmethod
    def _profile_key(profile_id):
        if not isinstance(profile_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', profile_id):
            raise ValueError('Invalid character identifier')
        return profile_id

    def _get(self, profile_id):
        key = self._profile_key(profile_id)
        if key not in self._profiles:
            if len(self._profiles) >= MAX_PROFILES:
                raise ValueError('Intent profile capacity reached')
            self._profiles[key] = _Profile(policy=self._default_policy.model_copy(deep=True))
        return self._profiles[key]

    def set_active_profile(self, profile_id):
        self._get(profile_id)
        if self.active_profile != profile_id:
            if self.active_profile in self._profiles:
                self._profiles[self.active_profile].selection_started = None
            self.active_profile = profile_id

    def set_policy(self, profile_id, policy):
        parsed = AudienceIntentPolicy.model_validate(policy)
        state = self._get(profile_id)
        state.policy = parsed.model_copy(deep=True)
        if not parsed.enabled:
            state.votes.clear()
            state.selection_started = None
        self._prune(state, self._now())
        return self.public(profile_id)

    @staticmethod
    def _key(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        text = str(value).strip()
        if not text or len(text) > 256:
            return None
        # Viewer IDs / raw message tokens never appear in public snapshots.
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    @staticmethod
    def _remember(history, key, now):
        history[key] = now
        history.move_to_end(key)
        while len(history) > MAX_HISTORY:
            history.popitem(last=False)

    def _prune(self, state, now):
        cutoff = now - state.policy.window_seconds
        while state.votes and state.votes[0].at <= cutoff:
            state.votes.popleft()
        # Dedupe remains through both the vote window and trigger cooldown.
        age = max(state.policy.window_seconds, state.policy.trigger_cooldown,
                  state.policy.viewer_cooldown, state.policy.selection_seconds)
        for history in (state.messages, state.viewers, state.selections):
            while history and next(iter(history.values())) <= now - age:
                history.popitem(last=False)
        if (state.selection_started is not None
                and now - state.selection_started >= state.policy.selection_seconds):
            state.selection_started = None

    @staticmethod
    def _counts(state):
        return {intent: sum(v.intent == intent for v in state.votes) for intent in INTENTS}

    def _eligible(self, state, now):
        if (not state.policy.enabled or state.selection_started is not None
                or state.last_trigger is not None
                and now - state.last_trigger < state.policy.trigger_cooldown):
            return None
        counts = self._counts(state)
        candidates = [name for name in INTENTS if counts[name] >= state.policy.threshold]
        if not candidates:
            return None
        # Highest demand wins; exact ties use the earlier threshold crossing.
        def rank(name):
            crossing = [vote.at for vote in state.votes if vote.intent == name][state.policy.threshold - 1]
            return (-counts[name], crossing, INTENTS.index(name))
        selected = min(candidates, key=rank)
        return {'intent': selected, 'votes': counts[selected], 'title': None}

    def record(self, profile_id, viewer_id, message_id, decision):
        """Admit one already-moderated audience turn, or return a reason.

        Model confidence is only a gate, never proof of truth. The caller still
        validates provenance, audience text, output moderation and character
        revision. Negative/quoted/hypothetical reports must set positive or
        current_request false. This component cannot infer those from a label.
        """
        state = self._get(profile_id)
        if self.active_profile is None:
            self.set_active_profile(profile_id)
        if profile_id != self.active_profile:
            return {'accepted': False, 'code': 'stale_profile'}
        now = self._now()
        self._prune(state, now)
        if not state.policy.enabled:
            return {'accepted': False, 'code': 'disabled'}
        viewer, message = self._key(viewer_id), self._key(message_id)
        if viewer is None or message is None:
            return {'accepted': False, 'code': 'missing_identity'}
        try:
            parsed = IntentDecision.model_validate(decision)
        except (ValueError, TypeError):
            return {'accepted': False, 'code': 'invalid_decision'}
        if message in state.messages:
            return {'accepted': False, 'code': 'duplicate_message'}
        self._remember(state.messages, message, now)
        if (parsed.intent == 'none' or not parsed.positive or not parsed.current_request
                or parsed.confidence < state.policy.minimum_confidence):
            return {'accepted': False, 'code': 'not_a_request'}
        if state.selection_started is not None:
            if parsed.intent != 'singing' or parsed.title is None:
                return {'accepted': False, 'code': 'awaiting_song'}
            last = state.selections.get(viewer)
            if last is not None and now - last < 2:
                return {'accepted': False, 'code': 'selection_cooldown'}
            self._remember(state.selections, viewer, now)
            return {'accepted': True, 'code': 'song_selection', 'intent': 'singing', 'title': parsed.title}
        last = state.viewers.get(viewer)
        if last is not None and now - last < state.policy.viewer_cooldown:
            return {'accepted': False, 'code': 'viewer_cooldown'}
        state.votes.append(_Vote(now, parsed.intent))
        self._remember(state.viewers, viewer, now)
        return {'accepted': True, 'code': 'vote', 'intent': parsed.intent,
                'title': parsed.title, 'counts': self._counts(state)}

    def peek_trigger(self, profile_id):
        state = self._get(profile_id)
        now = self._now()
        self._prune(state, now)
        if profile_id != self.active_profile:
            return None
        result = self._eligible(state, now)
        return dict(result) if result else None

    def consume_trigger(self, profile_id, *, stage_free, output_ready):
        """Reserve a trigger only when it can actually be announced/dispatched.

        Busy/offline states keep votes, subject to normal expiry. The dispatcher
        must recheck stage ownership after any awaits before starting a module.
        """
        if stage_free is not True or output_ready is not True:
            return None
        result = self.peek_trigger(profile_id)
        if not result:
            return None
        state = self._get(profile_id)
        state.last_trigger = self._now()
        state.votes.clear()
        return result

    def begin_song_selection(self, profile_id):
        state = self._get(profile_id)
        if self.active_profile is None:
            self.set_active_profile(profile_id)
        now = self._now()
        self._prune(state, now)
        if profile_id != self.active_profile or not state.policy.enabled:
            return False
        if state.selection_started is None:
            state.selection_started = now
        return True

    def song_selection(self, profile_id):
        state = self._get(profile_id)
        now = self._now()
        self._prune(state, now)
        if profile_id != self.active_profile or state.selection_started is None:
            return None
        return {'awaiting_song': True, 'remaining_seconds': max(0,
                state.policy.selection_seconds - (now - state.selection_started))}

    def complete_song_selection(self, profile_id):
        state = self._get(profile_id)
        state.selection_started = None

    def clear(self, profile_id):
        """Clear pending interaction when operator stops/disables a session."""
        state = self._get(profile_id)
        state.votes.clear()
        state.selection_started = None

    def public(self, profile_id):
        state = self._get(profile_id)
        now = self._now()
        self._prune(state, now)
        active = profile_id == self.active_profile
        awaiting = active and state.selection_started is not None
        return {'policy': state.policy.model_dump(), 'counts': self._counts(state),
                'pending': self._eligible(state, now) if active else None,
                'awaiting_song': awaiting,
                'selection_seconds_remaining': max(0, state.policy.selection_seconds -
                    (now - state.selection_started)) if awaiting else 0,
                'cooldown_remaining': 0 if state.last_trigger is None else max(0,
                    state.policy.trigger_cooldown - (now - state.last_trigger))}
