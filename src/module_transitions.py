"""Short character-voice transitions, prepared once and replayed from local MP3s."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import shutil
import tempfile
import time
from weakref import WeakValueDictionary

from mutagen import MutagenError
from mutagen.mp3 import MP3

logger = logging.getLogger(__name__)

PREPARE_TIMEOUT_SECONDS = 6
MAX_AUDIO_BYTES = 1024 * 1024
MAX_AUDIO_SECONDS = 12
SOFT_CACHE_LIMIT = 128
HARD_CACHE_LIMIT = 256
RECENT_CACHE_SECONDS = 3600
CACHE_VERSION = 'module-transitions-v1'
OWNED_FILE = re.compile(r'module_transition_[a-f0-9]{64}\.mp3')
WAIT_LINE = '不好意思，我的思考模型正在检修中，稍等一下下～'

TRANSITION_LINES = {
    'singing': {
        'start': ('那我唱首歌给你听，好不好？',
                  '嘿嘿，接下来把这首歌送给你。',
                  '准备好了吗？我要开始唱啦。'),
        'end': ('唱完啦，我唱得怎么样？',
                '呼，唱完啦，想不想夸夸我？',
                '这首歌送给你，喜欢吗？'),
    },
    'story': {
        'start': ('先坐舒服一点，给你讲个我们的小故事。',
                  '来，讲个小故事给你听，听完陪我聊聊。',
                  '那就先听我讲个故事吧，你准备好了吗？'),
        'end': ('故事讲完啦，你最喜欢哪一段？',
                '好啦，我们的小故事就讲到这里，你想说点什么？',
                '讲完啦，刚才那一段，有没有让你想到什么？'),
    },
}

# Voice controls only. Credentials, provider URLs and transport settings never
# enter cache names, clip metadata or logs. Pitch and pronunciation changes must
# invalidate a take even if a provider currently ignores a particular control.
VOICE_FIELDS = (
    'engine', 'voice', 'rate', 'pitch', 'volume', 'expressive', 'emotion_voice',
    'pronunciations', 'glm_voice', 'mimo_model', 'mimo_voice', 'mimo_style',
    'minimax_model', 'minimax_voice', 'minimax_pitch', 'delivery',
)
PROFILE_VOICE_FIELDS = (
    'enabled', 'provider', 'voice', 'rate', 'pitch', 'volume', 'expressive',
    'glm_voice', 'mimo_model', 'mimo_voice', 'mimo_style', 'minimax_voice',
    'minimax_pitch', 'delivery',
)


class ModuleTransitions:
    def __init__(self, pipeline, state):
        self.pipeline = pipeline
        self.state = state
        self._bags = {}
        self._last = {}
        self._locks = WeakValueDictionary()
        self._publication_lock = asyncio.Lock()
        self._tasks = set()
        self._closed = False
        self.last_notice = None

    def _capture(self):
        engine = self.pipeline.tts
        if engine is None or getattr(engine.config, 'engine', 'none') == 'none':
            return None
        profile_id = self.state.active_character
        profile = self.state.characters.profiles[profile_id]
        if not profile.voice.enabled:
            return None
        config = engine.config.model_copy(deep=True)
        params = engine._voice_params('happy')
        controls = {field: getattr(config, field) for field in VOICE_FIELDS}
        saved_controls = {field: getattr(profile.voice, field) for field in PROFILE_VOICE_FIELDS
                          if hasattr(profile.voice, field)}
        # Including the engine key also tracks its emotion-specific resolution.
        key = engine._cache_key('', params, config, 'happy')
        signature = hashlib.sha256(json.dumps([CACHE_VERSION, controls, saved_controls, params, key],
                                               ensure_ascii=False, sort_keys=True,
                                               allow_nan=False).encode()).hexdigest()
        return {'engine': engine, 'config': config, 'params': params,
                'profile_id': profile_id, 'voice_signature': signature,
                'root': Path(engine.output_dir).resolve()}

    def matches_current(self, clip):
        try:
            captured = self._capture()
            return bool(isinstance(clip, dict) and captured
                        and clip.get('profile_id') == captured['profile_id']
                        and clip.get('voice_signature') == captured['voice_signature'])
        except Exception:
            return False

    def _pick(self, profile_id, module_id, phase):
        key = (profile_id, module_id, phase)
        bag = self._bags.get(key)
        if not bag:
            bag = list(TRANSITION_LINES[module_id][phase])
            random.shuffle(bag)
            if len(bag) > 1 and bag[-1] == self._last.get(key):
                bag[0], bag[-1] = bag[-1], bag[0]
            self._bags[key] = bag
        text = bag.pop()
        self._last[key] = text
        return text

    @staticmethod
    def _duration(path):
        if path.is_symlink() or not 0 < path.stat().st_size <= MAX_AUDIO_BYTES:
            raise ValueError('Invalid transition audio file')
        seconds = float(MP3(path).info.length)
        if not math.isfinite(seconds) or not 0 < seconds <= MAX_AUDIO_SECONDS:
            raise ValueError('Invalid transition audio duration')
        return seconds

    def _lookup(self, path):
        try:
            seconds = self._duration(path)
        except (OSError, ValueError, TypeError, MutagenError):
            return None
        try:
            # Recently replayed takes are protected from cache pruning too.
            os.utime(path, None)
        except OSError:
            pass
        return seconds

    @staticmethod
    def _make_room(root, protected):
        files = []
        for path in root.glob('module_transition_*.mp3'):
            if not OWNED_FILE.fullmatch(path.name):
                continue
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        now = time.time()
        count = len(files)
        for modified, path in sorted(files):
            if count < SOFT_CACHE_LIMIT:
                break
            if path == protected or now - modified < RECENT_CACHE_SECONDS:
                continue
            try:
                path.unlink()
                count -= 1
            except OSError:
                continue
        return count < HARD_CACHE_LIMIT or protected.is_file()

    def _notice(self, module_id, phase, error):
        # Neither provider exception strings nor synthesis text enter logs.
        logger.warning('Module transition unavailable (%s); performance continues', type(error).__name__)
        self.last_notice = {'type': 'module_notice', 'module_id': module_id,
                            'message': '过场语音暂不可用，继续播放。',
                            'transition_phase': phase}
        queue = getattr(self.pipeline, 'result_queue', None)
        if queue is not None:
            try:
                queue.put_nowait(dict(self.last_notice))
            except (asyncio.QueueFull, AttributeError):
                pass

    def _clip(self, captured, text, path, seconds):
        return {'text': text, 'audio_url': '/audio/' + path.name, 'duration': seconds,
                'profile_id': captured['profile_id'],
                'voice_signature': captured['voice_signature'], 'emotion': 'happy'}

    async def _prepare(self, captured, text):
        engine = captured['engine']
        tts_key = engine._cache_key(text, captured['params'], captured['config'], 'happy')
        key = hashlib.sha256(json.dumps([CACHE_VERSION, tts_key, text, captured['voice_signature']],
                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        root = captured['root']
        root.mkdir(parents=True, exist_ok=True)
        target = root / ('module_transition_' + key + '.mp3')
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            seconds = self._lookup(target)
            if seconds is not None:
                return self._clip(captured, text, target, seconds)
            async with self._publication_lock:
                if not self._make_room(root, target):
                    raise ValueError('Transition cache is full')
            current = self._capture()
            if (not current or current['engine'] is not engine
                    or current['profile_id'] != captured['profile_id']
                    or current['voice_signature'] != captured['voice_signature']):
                return None
            audio_url = await engine.synthesize(text, 'happy')
            if not isinstance(audio_url, str):
                raise ValueError('Transition synthesis returned no audio')
            match = re.fullmatch(r'/audio/([A-Za-z0-9_-]+\.mp3)', audio_url)
            if not match:
                raise ValueError('Transition synthesis returned a nonlocal file')
            source = root / match.group(1)
            if not source.resolve().is_relative_to(root):
                raise ValueError('Transition synthesis path left its audio directory')
            self._duration(source)
            temporary = None
            try:
                async with self._publication_lock:
                    if not self._make_room(root, target):
                        raise ValueError('Transition cache is full')
                    descriptor, name = tempfile.mkstemp(prefix='.module_transition_', suffix='.part', dir=root)
                    os.close(descriptor)
                    temporary = Path(name)
                    shutil.copyfile(source, temporary)
                    seconds = self._duration(temporary)
                    os.replace(temporary, target)
                    temporary = None
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            return self._clip(captured, text, target, seconds)

    async def prepare(self, module_id, phase):
        if self._closed:
            return None
        phase = {'intro': 'start', 'outro': 'end'}.get(phase, phase)
        if module_id not in TRANSITION_LINES or phase not in {'start', 'end'}:
            return None
        task = None
        try:
            captured = self._capture()
            if captured is None:
                return None
            text = self._pick(captured['profile_id'], module_id, phase)
            safety = getattr(self.pipeline, 'safety', None)
            if safety is not None and not safety.allows_output(text):
                raise ValueError('Local transition review blocked playback')
            task = asyncio.create_task(self._prepare(captured, text))
            self._tasks.add(task)
            return await asyncio.wait_for(task, timeout=PREPARE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            if self._closed:
                return None
            raise
        except Exception as error:
            self._notice(module_id, phase, error)
            return None
        finally:
            if task is not None:
                self._tasks.discard(task)

    async def prepare_pair(self, module_id):
        intro, outro = await asyncio.gather(self.prepare(module_id, 'start'),
                                           self.prepare(module_id, 'end'))
        return {'intro': intro, 'outro': outro}

    async def prepare_wait(self):
        """A fixed, voice-matched wait line; no LLM or viewer text is involved."""
        if self._closed:
            return None
        task = None
        try:
            captured = self._capture()
            if captured is None or not self.pipeline.safety.allows_output(WAIT_LINE):
                return None
            task = asyncio.create_task(self._prepare(captured, WAIT_LINE))
            self._tasks.add(task)
            return await asyncio.wait_for(task, timeout=20)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning('Wait voice unavailable (%s)', type(error).__name__)
            return None
        finally:
            if task is not None:
                self._tasks.discard(task)

    async def close(self):
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
