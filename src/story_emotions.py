"""Estimate local facial cues from acting tags; speech audio remains untouched."""
from __future__ import annotations

import math
import re
import unicodedata


_ACTING_TAG = re.compile(r'（([^）\n]{0,80})）|\(([^)\n]{0,80})\)')
_EMOTION_WORDS = (
    ('surprised', ('惊讶', '惊喜', '没想到')),
    ('shy', ('心虚', '害羞', '不好意思', '紧张')),
    ('happy', ('轻笑', '开心', '得意', '温柔', '含笑', '含着笑', '俏皮', '憋笑', '轻快')),
    ('thinking', ('故作镇定', '故作严肃', '认真', '思考', '疑惑', '怀疑', '思索')),
    ('sad', ('难过', '委屈', '失落')),
    ('neutral', ('平静', '自然')),
)


def _spoken_units(text):
    spoken = _ACTING_TAG.sub('', text)
    return sum(unicodedata.category(char)[0] in {'L', 'N'} for char in spoken)


def story_expression_cues(text, duration):
    """Return at most 12 bounded cues, measured against the recorded duration.

    Tag positions use the fraction of spoken characters preceding each tag.
    These are approximate acting beats, not word timestamps or lip-sync data.
    Unknown tags affect neither the emotion nor the spoken-character count.
    Invalid durations produce no animation schedule.
    """
    if type(duration) not in (int, float):
        return []
    try:
        duration = float(duration)
    except (OverflowError, ValueError):
        return []
    if not math.isfinite(duration) or duration <= 0:
        return []
    result = [{'at': 0., 'emotion': 'neutral'}]
    if not isinstance(text, str) or not text.strip():
        return result
    total = _spoken_units(text)
    if not total:
        return result
    for match in _ACTING_TAG.finditer(text):
        tag = match.group(1) if match.group(1) is not None else match.group(2)
        emotion = next((name for name, words in _EMOTION_WORDS
                        if any(word in tag for word in words)), None)
        if emotion is None or emotion == result[-1]['emotion']:
            continue
        fraction = _spoken_units(text[:match.start()]) / total
        at = min(duration, round(duration * fraction, 3))
        result.append({'at': at, 'emotion': emotion})
        if len(result) == 12:
            break
    return result
