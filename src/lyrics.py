"""Bounded, local parsing of line-timed LRC lyrics; no audio processing."""
from __future__ import annotations

import re
import unicodedata
import json
import os
from pathlib import Path
import tempfile
import time

MAX_LRC_BYTES = 64 * 1024
MAX_LRC_CHARACTERS = 30000
MAX_LRC_LINES = 2000
MAX_LYRIC_CUES = 2000
MAX_LYRIC_LINE = 300
MAX_LRC_SECONDS = 3600
MAX_LYRIC_OFFSET = 30.

_TAG = re.compile(r'^\[([^\]\r\n]*)\]')
_TIME = re.compile(r'(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d)(?:\.(\d{1,3}))?')
_OFFSET = re.compile(r'offset\s*:\s*([+-]?\d+)', re.I)
_META = re.compile(r'([A-Za-z][A-Za-z0-9_-]{0,20})\s*:\s*(.*)', re.S)


def parse_lrc(text: str) -> dict:
    """Return raw cue times and a separate global offset in seconds.

    The returned/UI offset is positive for later display. Native LRC offset
    tags have the opposite sign: [offset:+500] advances lyrics by 0.5 seconds.
    Normalize that sign while keeping cue timestamps untouched, so operators
    can correct timing without rewriting or resampling the recording. Blank
    timed lines are retained as explicit caption clears.
    """
    if not isinstance(text, str):
        raise ValueError('歌词必须是文字格式的 LRC。')
    try:
        encoded = text.encode('utf-8')
    except UnicodeError:
        raise ValueError('歌词含有无效字符，请保存为 UTF-8 后重试。') from None
    if len(encoded) > MAX_LRC_BYTES or len(text) > MAX_LRC_CHARACTERS:
        raise ValueError('LRC 最多 64 KiB、30000 个字符。')
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if any(unicodedata.category(c) == 'Cc' and c not in '\n\t' for c in text):
        raise ValueError('LRC 含有不支持的控制字符。')
    lines = text.split('\n')
    if len(lines) > MAX_LRC_LINES:
        raise ValueError('LRC 最多 2000 行。')
    cues = []
    metadata = {}
    offset = None
    for line in lines:
        remainder = line.strip()
        times = []
        while remainder.startswith('['):
            tag = _TAG.match(remainder)
            if not tag:
                if re.match(r'^\[\s*(?:\d|offset)', remainder, re.I):
                    raise ValueError('LRC 时间标签格式无效。')
                break
            value = tag.group(1).strip()
            timing = _TIME.fullmatch(value)
            setting = _OFFSET.fullmatch(value)
            info = _META.fullmatch(value)
            if timing:
                hours, minutes, seconds, fraction = timing.groups()
                if hours is not None and int(minutes) >= 60:
                    raise ValueError('LRC 分钟必须小于 60。')
                at = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
                      + int(fraction or 0) / 10 ** len(fraction or '0'))
                if at > MAX_LRC_SECONDS:
                    raise ValueError('LRC 时间不能超过一小时。')
                times.append(round(at, 3))
            elif setting:
                milliseconds = int(setting.group(1))
                if abs(milliseconds) > MAX_LYRIC_OFFSET * 1000:
                    raise ValueError('LRC 时间偏移必须在正负 30 秒内。')
                if offset is not None and offset != milliseconds:
                    raise ValueError('LRC 包含互相冲突的 offset 标签。')
                offset = milliseconds
            elif info:
                key, content = info.groups()
                if key.lower() == 'offset':
                    raise ValueError('LRC offset 必须是整数毫秒。')
                if key.lower() in {'ti', 'ar'}:
                    if len(content) > 160:
                        raise ValueError('LRC 标题或作者信息过长。')
                    metadata['title' if key.lower() == 'ti' else 'artist'] = content.strip()
            elif re.match(r'^\d', value):
                raise ValueError('LRC 时间标签格式无效。')
            elif times:
                # A bracketed phrase after timestamps is lyric text, not a
                # header. Preserve it literally for safe textContent display.
                break
            remainder = remainder[tag.end():].lstrip()
        if not times:
            continue
        if len(remainder) > MAX_LYRIC_LINE:
            raise ValueError('每行歌词最多 300 个字符。')
        if len(cues) + len(times) > MAX_LYRIC_CUES:
            raise ValueError('LRC 最多 2000 个时间点。')
        cues.extend({'at': at, 'text': remainder} for at in times)
    if not cues or not any(cue['text'] for cue in cues):
        raise ValueError('没有找到带时间标签的歌词，例如 [00:12.50]歌词。')
    seen = set()
    ordered = []
    for cue in sorted(cues, key=lambda value: value['at']):
        identity = (cue['at'], cue['text'])
        if identity not in seen:
            seen.add(identity)
            ordered.append(cue)
    if sum(len(cue['text']) for cue in ordered) > MAX_LRC_CHARACTERS:
        raise ValueError('展开重复时间标签后的歌词最多 30000 个字符。')
    return {'cues': ordered, 'offset_seconds': -(offset or 0) / 1000, 'metadata': metadata}


def save_song_metadata(path: Path, item: dict):
    """Commit lyric metadata without exposing a partially written song file."""
    encoded = json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    descriptor, name = tempfile.mkstemp(prefix='.' + path.stem + '-lyrics-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(encoded)
            stream.flush()
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if attempt == 5 or getattr(error, 'winerror', None) not in {5, 32, 33}:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
