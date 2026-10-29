"""Convert existing ASR word timestamps into bounded, recording-timed lyrics.

This module performs no recognition, synthesis, or network requests. Timings are
recognizer estimates, not verified forced alignment. Missing word timings are
kept as coarse segment lines and flagged instead of inventing character timing.
"""
from __future__ import annotations

import math
import re
import unicodedata

from .lyrics import (MAX_LRC_CHARACTERS, MAX_LRC_SECONDS, MAX_LYRIC_CUES,
                     MAX_LYRIC_LINE, parse_lrc)


_STRONG_END = re.compile(r'[。！？!?；;][”’"\'）)]*$')
_SOFT_END = re.compile(r'[，,、：:][”’"\'）)]*$')
_CJK_SPACE = re.compile(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])')


def _number(value, label, *, lower=0, upper=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    value = float(value)
    if value < lower or (upper is not None and value > upper):
        raise ValueError(f'{label} is outside its supported range')
    return value


def _text(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text')
    if any(unicodedata.category(char) == 'Cc' and char not in '\r\n\t' for char in value):
        raise ValueError(f'{label} contains a control character')
    value = unicodedata.normalize('NFC', value)
    return re.sub(r'\s+', ' ', value)


def _joined(left, right):
    """Keep English word spacing while removing ASR spaces between CJK tokens."""
    if not left:
        return right.strip()
    if not right.strip():
        return left
    if (right[:1].isspace() and left[-1:].isascii() and left[-1:].isalnum()
            and right.lstrip()[:1].isascii() and right.lstrip()[:1].isalnum()):
        return left.rstrip() + ' ' + right.strip()
    return _CJK_SPACE.sub('', left.rstrip() + right.lstrip())


def _centiseconds(value):
    return int(math.floor(value * 100 + .5))


def build_lyric_cues(segments, duration, max_chars=18, gap_seconds=1.2, *,
                     max_line_seconds=8.0, clear_hold_seconds=.15):
    """Return ``{cues, lines, warnings}`` without moving the audio timeline.

    ``segments`` contains faster-whisper segment dictionaries. When available,
    ``words`` have ``start``, ``end``, ``word``, and ``probability`` fields. Cues
    are rounded to LRC centiseconds; line records retain the original timings and
    confidence/review flags. A long indivisible ASR token remains intact rather
    than gaining fabricated per-character timing.
    """
    duration = _number(duration, 'duration', lower=.01, upper=MAX_LRC_SECONDS)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= MAX_LYRIC_LINE:
        raise ValueError('max_chars must be between 1 and 300')
    gap_seconds = _number(gap_seconds, 'gap_seconds', lower=.01, upper=60)
    max_line_seconds = _number(max_line_seconds, 'max_line_seconds', lower=.1, upper=60)
    clear_hold_seconds = _number(clear_hold_seconds, 'clear_hold_seconds', upper=2)
    lines, warnings, pending = [], [], []
    last_segment_start, last_word_start_global, total_characters, total_words = -1., -1., 0, 0

    def flush():
        if not pending:
            return
        text = ''
        for token in pending:
            text = _joined(text, token['text'])
        text = text.strip()
        if not text:
            pending.clear()
            return
        if len(text) > MAX_LYRIC_LINE:
            raise ValueError('A lyric line exceeds the supported 300 characters')
        weights = [max(1, len(token['text'].strip())) for token in pending]
        known = [(token['confidence'], weight) for token, weight in zip(pending, weights)
                 if token['confidence'] is not None]
        confidence = (sum(score * weight for score, weight in known) / sum(weight for _, weight in known)) if known else None
        reasons = list(dict.fromkeys(reason for token in pending for reason in token['reasons']))
        if confidence is None:
            reasons.append('unknown_confidence')
        elif confidence < .65:
            reasons.append('low_confidence')
        if len(text) > max_chars:
            reasons.append('indivisible_long_token')
        start, end = pending[0]['start'], max(token['end'] for token in pending)
        if end - start > max_line_seconds:
            reasons.append('coarse_or_long_timing')
        if end == start:
            reasons.append('zero_length_timing')
        line = {'start': start, 'end': end, 'text': text,
                'confidence': round(confidence, 6) if confidence is not None else None,
                'review_required': bool(reasons), 'review_reasons': list(dict.fromkeys(reasons))}
        # Identical onset estimates cannot display two independent LRC lines.
        # Merge them, preserving text order, rather than inventing an onset.
        if lines and _centiseconds(lines[-1]['start']) == _centiseconds(start):
            previous = lines[-1]
            if previous['text'] != text:
                previous['text'] = _joined(previous['text'], ' ' + text)
                if len(previous['text']) > MAX_LYRIC_LINE:
                    raise ValueError('Coincident lyric lines exceed 300 characters')
            previous['end'] = max(previous['end'], line['end'])
            scores = [score for score in (previous['confidence'], line['confidence']) if score is not None]
            previous['confidence'] = min(scores) if scores else None
            previous['review_reasons'] = list(dict.fromkeys([
                *previous['review_reasons'], *line['review_reasons'], 'coincident_onsets']))
            previous['review_required'] = True
        else:
            lines.append(line)
        pending.clear()

    for segment_index, segment in enumerate(segments):
        if segment_index >= MAX_LYRIC_CUES:
            raise ValueError('Too many transcription segments')
        if not isinstance(segment, dict):
            raise ValueError('Each transcription segment must be a dictionary')
        start = _number(segment.get('start'), 'segment start', upper=duration)
        end = _number(segment.get('end'), 'segment end', lower=start, upper=duration)
        if start < last_segment_start:
            raise ValueError('Transcription segments must be in chronological order')
        last_segment_start = start
        probability = None
        if segment.get('avg_logprob') is not None:
            logprob = _number(segment['avg_logprob'], 'avg_logprob', lower=-100, upper=0)
            probability = math.exp(logprob)
        reasons = []
        if segment.get('no_speech_prob') is not None:
            no_speech = _number(segment['no_speech_prob'], 'no_speech_prob', upper=1)
            if no_speech > .6:
                reasons.append('high_no_speech_probability')
        words = segment.get('words')
        if words is not None and not isinstance(words, (list, tuple)):
            raise ValueError('Segment words must be a list')
        if not words:
            raw_text = _text(segment.get('text', ''), 'segment text')
            if not raw_text.strip():
                continue
            words = [{'start': start, 'end': end, 'word': raw_text, 'probability': probability}]
            reasons.append('missing_word_timestamps')
            warnings.append(f'segment_{segment_index + 1}_missing_word_timestamps')
        last_word_start = -1.
        for word in words:
            total_words += 1
            if total_words > 20000:
                raise ValueError('Too many recognized words')
            if not isinstance(word, dict):
                raise ValueError('Each word must be a dictionary')
            word_start = _number(word.get('start'), 'word start', upper=duration)
            word_end = _number(word.get('end'), 'word end', lower=word_start, upper=duration)
            if word_start < last_word_start or word_start < last_word_start_global:
                raise ValueError('Word timestamps must be in chronological order')
            last_word_start = word_start
            last_word_start_global = word_start
            text = _text(word.get('word', ''), 'word text')
            total_characters += len(text)
            if total_characters > MAX_LRC_CHARACTERS:
                raise ValueError('Recognized text exceeds the LRC character limit')
            if not text.strip():
                continue
            score = word.get('probability')
            score = probability if score is None else _number(score, 'word probability', upper=1)
            token = {'start': word_start, 'end': word_end, 'text': text,
                     'confidence': score, 'reasons': reasons.copy()}
            if score is None:
                token['reasons'].append('unknown_word_confidence')
            if pending:
                candidate = ''
                for existing in pending:
                    candidate = _joined(candidate, existing['text'])
                candidate = _joined(candidate, text)
                pause = word_start - pending[-1]['end']
                if (len(candidate) > max_chars or word_end - pending[0]['start'] > max_line_seconds
                        or pause >= min(.65, gap_seconds)):
                    flush()
            pending.append(token)
            assembled = ''
            for existing in pending:
                assembled = _joined(assembled, existing['text'])
            if (_STRONG_END.search(assembled) or
                    (_SOFT_END.search(assembled) and len(assembled) >= 4
                     and word_end - pending[0]['start'] >= .8)):
                flush()
        # ASR segment boundaries are useful phrase boundaries, especially when
        # punctuation is absent. They do not add timing or interpolation.
        flush()
    if not lines:
        raise ValueError('No recognized lyrics were available')
    cues = []
    maximum_tick = int(math.floor(duration * 100 + 1e-7))
    for index, line in enumerate(lines):
        at = min(maximum_tick, _centiseconds(line['start'])) / 100
        cues.append({'at': at, 'text': line['text']})
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if next_line is None or next_line['start'] - line['end'] > gap_seconds:
            clear = min(maximum_tick, _centiseconds(min(duration, line['end'] + clear_hold_seconds))) / 100
            next_at = min(maximum_tick, _centiseconds(next_line['start'])) / 100 if next_line else None
            if clear > at and (next_at is None or clear < next_at):
                cues.append({'at': clear, 'text': ''})
        for reason in line['review_reasons']:
            warnings.append(f'line_{index + 1}_{reason}')
    if len(cues) > MAX_LYRIC_CUES:
        raise ValueError('Too many lyric cues')
    return {'cues': cues, 'lines': lines, 'warnings': list(dict.fromkeys(warnings))}


def to_lrc(cues, title, artist='Astra'):
    """Serialize literal line cues with centisecond timestamps and offset zero."""
    title = _text(title, 'title').strip()
    artist = _text(artist, 'artist').strip()
    if any(char in title + artist for char in '[]') or len(title) > 160 or len(artist) > 160:
        raise ValueError('LRC title and artist must be plain text of at most 160 characters')
    result = [f'[ti:{title}]', f'[ar:{artist}]', '[offset:0]']
    expected, previous = [], -1
    for index, cue in enumerate(cues):
        if index >= MAX_LYRIC_CUES or not isinstance(cue, dict):
            raise ValueError('Invalid or excessive lyric cues')
        seconds = _number(cue.get('at'), 'cue timestamp', upper=MAX_LRC_SECONDS)
        tick = _centiseconds(seconds)
        if tick < previous:
            raise ValueError('Lyric cues must be chronological')
        previous = tick
        text = _text(cue.get('text'), 'cue text').strip()
        if len(text) > MAX_LYRIC_LINE:
            raise ValueError('Each lyric line must be at most 300 characters')
        minutes, hundredths = divmod(tick, 6000)
        whole_seconds, hundredths = divmod(hundredths, 100)
        result.append(f'[{minutes:02d}:{whole_seconds:02d}.{hundredths:02d}]{text}')
        identity = {'at': tick / 100, 'text': text}
        if identity not in expected:
            expected.append(identity)
    serialized = '\n'.join(result) + '\n'
    parsed = parse_lrc(serialized)
    if parsed['cues'] != expected or parsed['offset_seconds'] != 0:
        raise ValueError('Recognized text contains an ambiguous LRC tag')
    return serialized
