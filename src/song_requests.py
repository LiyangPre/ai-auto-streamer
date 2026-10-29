"""Bounded, local song requests; viewer input never creates audio or model calls."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import re
import time
import unicodedata
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

MAX_REQUESTS = 32
MAX_HISTORY = 512


class SongRequestPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')

    chat_requests: bool = Field(default=True, strict=True)
    auto_play: bool = Field(default=True, strict=True)
    max_queue: int = Field(default=10, ge=1, le=MAX_REQUESTS, strict=True)
    user_cooldown: float = Field(default=90, ge=5, le=3600, allow_inf_nan=False)
    song_cooldown: float = Field(default=300, ge=0, le=3600, allow_inf_nan=False)
    intermission_seconds: float = Field(default=15, ge=5, le=300, allow_inf_nan=False)
    blocked_titles: list[str] = Field(default_factory=list, max_length=MAX_REQUESTS, strict=True)

    @field_validator('blocked_titles')
    @classmethod
    def valid_blocked_titles(cls, values):
        titles, seen = [], set()
        for title in values:
            if not 1 <= len(title) <= 80:
                raise ValueError('Blocked song titles must contain 1–80 characters.')
            title = unicodedata.normalize('NFKC', title).strip()
            key = normalized_title(title)
            if not key or len(title) > 80:
                raise ValueError('Blocked song titles cannot be empty.')
            if key not in seen:
                titles.append(title)
                seen.add(key)
        return titles


def normalized_title(text):
    value = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(c for c in value if not c.isspace() and unicodedata.category(c)[0] not in 'PSC')


class SongRequestQueue:
    """One synchronous ingress handler and one bounded queue for all profiles.

    The playback owner checks stage availability before pop_next(). A popped
    entry reserves its place until mark_played() or requeue_front(), so a failed
    playback start cannot lose a request or let concurrent admissions overfill.
    No task, file write, synthesis or provider call happens here.
    """

    def __init__(self, studio, state):
        self.studio = studio
        self.state = state
        self.changed = asyncio.Event()
        self.change_event = self.changed
        self.items = []
        self.inflight = None
        self._last_users = OrderedDict()
        self._last_songs = OrderedDict()
        self._last_results = OrderedDict()

    @property
    def profile_id(self):
        return self.state.active_character if self.state is not None else 'astra'

    def policy(self, profile_id=None):
        if self.state is None:
            return SongRequestPolicy()
        profile = self.state.characters.profiles[profile_id or self.profile_id]
        return SongRequestPolicy.model_validate(profile.module_options.get('singing', {}))

    def is_blocked(self, song_or_title):
        """A pause follows the character and title, including future imports."""
        try:
            if isinstance(song_or_title, dict):
                title = song_or_title.get('title')
                profile_id = song_or_title.get('profile_id') or self.profile_id
            else:
                title, profile_id = song_or_title, self.profile_id
            if not isinstance(title, str) or not normalized_title(title):
                return True
            blocked = {normalized_title(value) for value in self.policy(profile_id).blocked_titles}
            return normalized_title(title) in blocked
        except (KeyError, TypeError, ValueError):
            # Corrupt policy cannot silently enable an explicitly paused song.
            return True

    def prune_blocked(self):
        """Drop paused waiting songs while preserving other characters' queues."""
        songs = {song['id']: song for song in self._songs()}
        kept = [entry for entry in self.items
                if entry['profile_id'] != self.profile_id
                or not self.is_blocked(songs.get(entry['song_id'], entry))]
        removed = len(self.items) - len(kept)
        if removed:
            self.items[:] = kept
            self._result(False, 'banned', '已移除队列中被主播暂停的歌曲。', removed=removed)
        return removed

    def _remember(self, history, key, value, limit=MAX_HISTORY):
        history[key] = value
        history.move_to_end(key)
        while len(history) > limit:
            history.popitem(last=False)

    def _result(self, accepted, code, message, **extra):
        result = {'accepted': accepted, 'code': code, 'message': message, **extra}
        self._remember(self._last_results, self.profile_id, result, limit=32)
        self.changed.set()
        return dict(result)

    @staticmethod
    def _public_entry(entry):
        return {key: entry[key] for key in
                ('id', 'song_id', 'title', 'requester', 'profile_id', 'queued_at')}

    def public(self):
        return {'policy': self.policy().model_dump(),
                'queue': [self._public_entry(entry) for entry in self.items
                          if entry['profile_id'] == self.profile_id],
                'last_result': dict(self._last_results.get(self.profile_id, {}))}

    def _safe(self, text):
        try:
            return isinstance(text, str) and self.studio.pipeline.safety.allows_input(text)
        except Exception:
            # A local moderation failure must not admit unchecked viewer data.
            return False

    def _songs(self):
        return [song for song in self.studio.catalog()
                if song.get('profile_id') == self.profile_id
                and isinstance(song.get('title'), str)
                and re.fullmatch(r'[a-f0-9]{32}', str(song.get('id', '')))]

    def _aliases(self):
        names = {'Astra', '阿斯特拉', '星星'}
        if self.state is not None:
            identity = self.state.characters.profiles[self.profile_id].identity
            names.update(value for value in (getattr(identity, 'name', ''),
                                             getattr(identity, 'spoken_name', ''))
                         if isinstance(value, str) and value)
        return '|'.join(re.escape(value) for value in sorted(names, key=len, reverse=True))

    def on_message(self, message):
        if getattr(message, 'msg_type', None) != 'danmaku':
            return False
        raw = getattr(message, 'content', '')
        if not isinstance(raw, str):
            return False
        # Match only explicit commands. Ordinary questions about stories and
        # songs continue through chat rather than becoming fuzzy song matches.
        recognized = False
        try:
            text = unicodedata.normalize('NFKC', raw[:512]).strip()
            match = re.match(r'^(?:(?:' + self._aliases() + r')[\s,，:：]*)?'
                             r'(?:点歌|点一首|唱一首|来一首|/song(?=\s|:|$))'
                             r'[\s:：]*(.*)$', text, flags=re.I | re.S)
            if not match or not self.policy().chat_requests:
                return False
            recognized = True
            name = getattr(message, 'user_name', '')
            if len(raw) > 512 or not self._safe(raw) or not self._safe(name):
                self._result(False, 'blocked', '这条点歌请求未通过本地检查。')
                return True
            title = normalized_title(match.group(1))
            if not title:
                self._result(False, 'missing_title', '请发送“点歌 歌曲名”，从当前角色的已完成歌库中选择。')
                return True
            if self.is_blocked(match.group(1)):
                self._result(False, 'banned', '这首歌已被主播暂停，暂不接受点歌。')
                return True
            matches = [song for song in self._songs() if normalized_title(song['title']) == title]
            if not matches:
                self._result(False, 'not_found', '当前角色歌库中还没有这首歌，请从已完成曲目点歌。')
            elif len(matches) > 1:
                self._result(False, 'ambiguous', '歌库中存在多个同名版本，请由主播选择具体曲目。')
            else:
                self.enqueue_id(matches[0]['id'], getattr(message, 'user_id', 0), name, source='chat')
            return True
        except Exception as error:
            # Ingress is synchronous and shared with paid events/chat. A bad
            # catalog or policy must never tear down the platform collector.
            logger.warning('Song request could not be handled (%s)', type(error).__name__)
            if recognized:
                self._result(False, 'unavailable', '点歌暂时不可用，请稍后再试。')
            return recognized

    def enqueue_id(self, song_id, user_id, user_name, source='controller'):
        policy = self.policy()
        if source == 'chat' and not policy.chat_requests:
            return self._result(False, 'disabled', '弹幕点歌已暂停。')
        if not self._safe(user_name):
            return self._result(False, 'blocked', '这条点歌请求未通过本地检查。')
        song = next((song for song in self._songs() if song['id'] == song_id), None)
        if song is None:
            return self._result(False, 'not_found', '当前角色歌库中还没有这首歌，请从已完成曲目点歌。')
        if self.is_blocked(song):
            return self._result(False, 'banned', '这首歌已被主播暂停，暂不接受点歌。')
        if not self._safe(song['title']):
            return self._result(False, 'blocked', '这首曲目暂时不能加入点歌队列。')
        profile_id = self.profile_id
        reserved = self.items + ([self.inflight] if self.inflight else [])
        playback = getattr(self.studio, 'playback', None) or {}
        if (any(entry['profile_id'] == profile_id and entry['song_id'] == song_id for entry in reserved)
                or playback.get('song', {}).get('id') == song_id):
            return self._result(False, 'duplicate', '这首歌已经在队列中或正在演唱。')
        if len(reserved) >= MAX_REQUESTS or sum(entry['profile_id'] == profile_id for entry in reserved) >= policy.max_queue:
            return self._result(False, 'full', '点歌队列已满，请稍后再点。')
        now = time.monotonic()
        user_key = (profile_id, str(user_id)[:100])
        last_user = self._last_users.get(user_key)
        if source == 'chat' and last_user is not None and now - last_user < policy.user_cooldown:
            return self._result(False, 'user_cooldown', '点歌太快啦，请稍等一会儿再点。')
        last_song = self._last_songs.get((profile_id, song_id))
        if last_song is not None and now - last_song < policy.song_cooldown:
            return self._result(False, 'song_cooldown', '这首歌刚刚唱过，稍后再点吧。')
        name = ''.join(char for char in user_name if char.isprintable()).strip()[:64] or '观众'
        entry = {'id': uuid.uuid4().hex, 'song_id': song_id, 'title': song['title'][:80],
                 'requester': name, 'profile_id': profile_id, 'queued_at': time.time(),
                 'user_id': user_id, 'source': source}
        self.items.append(entry)
        if source == 'chat':
            self._remember(self._last_users, user_key, now)
        position = sum(item['profile_id'] == profile_id for item in self.items)
        return self._result(True, 'queued', '已加入点歌队列：' + entry['title'],
                            request_id=entry['id'], song_id=song_id, title=entry['title'], position=position)

    def pop_next(self):
        if self.inflight is not None:
            return None
        songs = {song['id']: song for song in self._songs()}
        for entry in tuple(self.items):
            if entry['profile_id'] != self.profile_id:
                continue
            self.items.remove(entry)
            if self.is_blocked(songs.get(entry['song_id'], entry)):
                self._result(False, 'banned', '队列中的一首歌曲已暂停，已跳过。')
                continue
            if entry['song_id'] not in songs or not self._safe(songs[entry['song_id']]['title']):
                self._result(False, 'unavailable', '队列中的一首曲目已不可用，已跳过。')
                continue
            self.inflight = entry
            self.changed.set()
            return dict(entry)
        return None

    def requeue_front(self, entry):
        if not self.inflight or self.inflight['id'] != entry.get('id'):
            return False
        self.items.insert(0, self.inflight)
        self.inflight = None
        self.changed.set()
        return True

    def mark_played(self, entry):
        if not self.inflight or self.inflight['id'] != entry.get('id'):
            return False
        owned = self.inflight
        self.inflight = None
        self._record_played(owned)
        return True

    def _record_played(self, owned):
        self._remember(self._last_songs, (owned['profile_id'], owned['song_id']), time.monotonic())
        # This is called only after stage acquisition; an operator may have
        # switched profiles while playback was being attempted. Scope its
        # result to the requesting profile without changing the active one.
        result = {'accepted': True, 'code': 'playing', 'message': '开始演唱：' + owned['title'],
                  'song_id': owned['song_id'], 'title': owned['title']}
        self._remember(self._last_results, owned['profile_id'], result, limit=32)
        self.changed.set()

    def mark_song_played(self, song_id):
        """Record a successful operator start, satisfying its queued request."""
        song = next((song for song in self._songs() if song['id'] == song_id), None)
        if song is None or not self._safe(song['title']):
            return False
        profile_id = self.profile_id
        self.items[:] = [entry for entry in self.items
                         if not (entry['profile_id'] == profile_id and entry['song_id'] == song_id)]
        self._record_played({'profile_id': profile_id, 'song_id': song_id, 'title': song['title'][:80]})
        return True

    def remove(self, request_id):
        entry = next((item for item in self.items
                      if item['id'] == request_id and item['profile_id'] == self.profile_id), None)
        if entry is None:
            return False
        self.items.remove(entry)
        self._result(True, 'removed', '已移除这条点歌请求。')
        return True

    def clear(self):
        kept = [entry for entry in self.items if entry['profile_id'] != self.profile_id]
        count = len(self.items) - len(kept)
        self.items[:] = kept
        self._result(True, 'cleared', '已清空当前角色的待播点歌。')
        return count
