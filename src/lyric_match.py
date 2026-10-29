"""Conservatively match reference LRC timing to an existing local transcript.

This is a recording/version check, not forced alignment or speech recognition.
Only reference text is returned; no text is invented from titles or ASR errors.
Callers may normalize traditional Chinese beforehand. A weak or non-uniform
match returns no cues so it cannot accidentally become a default lyric file.
"""
from __future__ import annotations

from array import array
from difflib import SequenceMatcher
import math
import re
from statistics import median
import unicodedata

from .lyrics import MAX_LRC_SECONDS, MAX_LYRIC_CUES, MAX_LYRIC_LINE


_CREDIT = re.compile(
    r'^(?:(?:作词|作曲|编曲|填词|词曲|演唱|歌手|制作人|制作|混音|录音|监制|'
    r'歌词|音乐总监|发行|出品|版权|词|曲|op|sp|producer|lyricist|composer|'
    r'arranger|vocal|lyrics|lrc)\s*[:：]|(?:作词|作曲|编曲|演唱)\s*[/／])', re.I)
_MAX_FIT_LINES = 400
_MAX_FIT_CHARACTERS = 12000


def normalize_text(text):
    """Keep letters/numbers for comparison; preserve original output separately."""
    text = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(char for char in text if unicodedata.category(char)[0] in 'LN')


def _number(value, name, *, lower=0., upper=MAX_LRC_SECONDS):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    if value < lower or value > upper:
        raise ValueError(f'{name} is out of bounds')
    return float(value)


def _text(value):
    if not isinstance(value, str) or len(value) > MAX_LYRIC_LINE:
        raise ValueError('Lyric text must contain at most 300 characters')
    if any(unicodedata.category(char) == 'Cc' for char in value):
        raise ValueError('Lyric text cannot contain control characters')
    return value


def _inputs(cues, asr_lines, duration, text_normalizer=None):
    duration = _number(duration, 'duration', lower=.01)
    if not isinstance(cues, (tuple, list)) or not isinstance(asr_lines, (tuple, list)):
        raise ValueError('Cues and ASR lines must be lists')
    if len(cues) > MAX_LYRIC_CUES or len(asr_lines) > MAX_LYRIC_CUES:
        raise ValueError('Too many lyric lines')
    raw, reference, observed = [], [], []
    def comparison(text):
        if text_normalizer is not None:
            text = text_normalizer(text)
            if not isinstance(text,str):
                raise ValueError('The comparison normalizer must return text')
        return normalize_text(text)
    previous = -1.
    for index, item in enumerate(cues):
        if not isinstance(item, dict):
            raise ValueError('Each cue must be a dictionary')
        at = _number(item.get('at'), 'reference timestamp')
        text = _text(item.get('text'))
        if at < previous:
            raise ValueError('Reference cues must be chronological')
        previous = at
        raw.append({'at': at, 'text': text})
        norm = comparison(text)
        if norm and not _CREDIT.match(unicodedata.normalize('NFKC', text).strip()):
            reference.append({'at': at, 'text': norm, 'raw_index': index})
    previous = -1.
    for index, item in enumerate(asr_lines):
        if not isinstance(item, dict):
            raise ValueError('Each ASR line must be a dictionary')
        start = _number(item.get('start'), 'ASR start', upper=duration)
        end = _number(item.get('end'), 'ASR end', lower=start, upper=duration)
        if start < previous:
            raise ValueError('ASR lines must be chronological')
        previous = start
        norm = comparison(_text(item.get('text')))
        confidence = item.get('confidence')
        if confidence is not None:
            confidence = _number(confidence, 'confidence', upper=1.)
        if norm:
            observed.append({'at':start, 'end':end, 'text':norm,
                             'confidence':confidence, 'raw_index':index})
    if len(reference) > _MAX_FIT_LINES or len(observed) > _MAX_FIT_LINES:
        raise ValueError('Too many lines for conservative version matching')
    if sum(len(row['text']) for row in reference + observed) > _MAX_FIT_CHARACTERS:
        raise ValueError('Too much text for conservative version matching')
    return raw, reference, observed, duration


def _ratio(left, right):
    # Blocks are short lyric phrases; disable popularity heuristics so repeated
    # choruses retain their text instead of being discarded as common tokens.
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _edit_ratio(left, right):
    """Full-text edit distance avoids choosing a wrong longest chorus block."""
    if left == right:
        return 1.
    if not left or not right:
        return 0.
    if len(left)*len(right) > 8_000_000:
        return None
    if len(left) < len(right):
        left,right = right,left
    previous = list(range(len(right)+1))
    for i,first in enumerate(left,1):
        row = [i]
        for j,second in enumerate(right,1):
            row.append(min(previous[j]+1,row[-1]+1,previous[j-1]+(first != second)))
        previous = row
    return 1.-previous[-1]/len(left)


def _joined(rows, end, count, *, observed=False):
    group = rows[end-count:end]
    for first,second in zip(group,group[1:]):
        gap = second['at'] - (first['end'] if observed else first['at'])
        if gap > (3. if observed else 10.):
            return None
    if observed and group[-1]['end']-group[0]['at'] > 20.:
        return None
    return ''.join(row['text'] for row in group)


def _align(reference, observed):
    """Needleman-Wunsch with optional adjacent split/merged phrase matches."""
    n, m = len(reference), len(observed)
    scores = [array('d', [float('-inf')]) * (m+1) for _ in range(n+1)]
    trace = [bytearray(m+1) for _ in range(n+1)]
    scores[0][0] = 0.
    for i in range(1, n+1):
        scores[i][0] = scores[i-1][0] - .24 * len(reference[i-1]['text'])
        trace[i][0] = 1
    for j in range(1, m+1):
        scores[0][j] = scores[0][j-1] - .18 * len(observed[j-1]['text'])
        trace[0][j] = 2
    options = ((1, 1, 3), (1, 2, 4), (2, 1, 5), (1, 3, 6))
    for i in range(1, n+1):
        for j in range(1, m+1):
            best = scores[i-1][j] - .24 * len(reference[i-1]['text'])
            code = 1
            skip = scores[i][j-1] - .18 * len(observed[j-1]['text'])
            if skip > best:
                best, code = skip, 2
            for r_count, a_count, candidate_code in options:
                if r_count > i or a_count > j:
                    continue
                left = _joined(reference, i, r_count)
                right = _joined(observed, j, a_count, observed=True)
                if left is None or right is None:
                    continue
                ratio = _ratio(left, right)
                if ratio < .55:
                    continue
                value = (scores[i-r_count][j-a_count]
                         + (ratio-.52) * (len(left)+len(right))
                         - .5 * (r_count+a_count-2))
                if value > best:
                    best, code = value, candidate_code
            scores[i][j], trace[i][j] = best, code
    groups = []
    i, j = n, m
    while i or j:
        code = trace[i][j]
        if code == 1:
            i -= 1
        elif code == 2:
            j -= 1
        else:
            r_count, a_count = {3:(1,1), 4:(1,2), 5:(2,1), 6:(1,3)}[code]
            r_start, a_start = i-r_count, j-a_count
            left = ''.join(row['text'] for row in reference[r_start:i])
            right = ''.join(row['text'] for row in observed[a_start:j])
            groups.append((r_start,i,a_start,j,_ratio(left,right)))
            i, j = r_start, a_start
    return list(reversed(groups))


def _percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values)-1, int(math.ceil(len(values)*fraction))-1)]


def match_reference(cues, asr_lines, duration, *, text_normalizer=None):
    """Return a validated uniform reference-timing correction, or no cues.

    At least six strong, monotonic phrase anchors must cover most of both lyric
    timelines. A fixed offset is preferred. A scale within 0.97–1.03 is used only
    when it substantially reduces residual timing error. Recognition confidence
    and these checks do not make this a word-perfect or human-verified alignment.
    A trusted caller may supply a comparison-only normalizer (for example Chinese
    phonetic normalization). It never changes the reference text returned in cues.
    """
    raw, reference, observed, duration = _inputs(cues, asr_lines, duration, text_normalizer)
    result = {'cues':[], 'matched_anchors':0, 'median_error_seconds':duration,
              'offset_seconds':0., 'scale':1., 'coverage':0., 'accepted':False,
              'reason':'insufficient_lyrics'}
    if len(reference) < 6 or len(observed) < 6:
        return result
    text_ratio = _edit_ratio(''.join(row['text'] for row in reference),
                            ''.join(row['text'] for row in observed))
    if text_ratio is None:
        result['reason'] = 'comparison_budget_exceeded'
        return result
    if text_ratio < .72:
        result['reason'] = 'weak_text_match'
        return result
    groups = _align(reference, observed)
    matched_r, matched_a, anchors = set(), set(), []
    for r_start,r_end,a_start,a_end,ratio in groups:
        if ratio >= .65:
            matched_r.update(range(r_start,r_end))
            matched_a.update(range(a_start,a_end))
        first_r, first_a = reference[r_start], observed[a_start]
        left = ''.join(row['text'] for row in reference[r_start:r_end])
        right = ''.join(row['text'] for row in observed[a_start:a_end])
        first_block = SequenceMatcher(None,left,right,autojunk=False).get_matching_blocks()[0]
        confidence = first_a['confidence']
        if (ratio >= .78 and min(len(left),len(right)) >= 5
                and (confidence is None or confidence >= .55)
                and first_block.a <= 2 and first_block.b <= 2):
            anchors.append((first_r['at'],first_a['at'],first_r['text']))
    reference_coverage = sum(len(reference[i]['text']) for i in matched_r) / sum(len(row['text']) for row in reference)
    observed_coverage = sum(len(observed[i]['text']) for i in matched_a) / sum(len(row['text']) for row in observed)
    result['coverage'] = round(min(reference_coverage,observed_coverage),6)
    result['matched_anchors'] = len(anchors)
    if len(anchors) < 6 or len({anchor[2] for anchor in anchors}) < 4:
        result['reason'] = 'insufficient_independent_anchors'
        return result
    if min(reference_coverage,observed_coverage) < .75:
        result['reason'] = 'incomplete_lyric_coverage'
        return result
    reference_span = reference[-1]['at']-reference[0]['at']
    observed_span = observed[-1]['at']-observed[0]['at']
    if (reference_span <= 0 or observed_span <= 0
            or anchors[-1][0]-anchors[0][0] < reference_span*.72
            or anchors[-1][1]-anchors[0][1] < observed_span*.72):
        result['reason'] = 'insufficient_timeline_coverage'
        return result

    fixed_offset = median(y-x for x,y,_ in anchors)
    fixed_errors = [abs(y-x-fixed_offset) for x,y,_ in anchors]
    slope_pairs = [(b[1]-a[1])/(b[0]-a[0]) for i,a in enumerate(anchors)
                   for b in anchors[i+1:] if b[0]-a[0] >= max(15.,reference_span/4)]
    slope = median(slope_pairs) if slope_pairs else 1.
    offset = median(y-slope*x for x,y,_ in anchors)
    scaled_errors = [abs(y-slope*x-offset) for x,y,_ in anchors]
    scale = 1.
    if (.97 <= slope <= 1.03 and abs(slope-1) >= .0008
            and median(scaled_errors) < median(fixed_errors)*.75
            and median(fixed_errors)-median(scaled_errors) >= .08):
        scale = slope
    else:
        offset = fixed_offset
    errors = [abs(y-scale*x-offset) for x,y,_ in anchors]
    middle = median(errors)
    inlier_limit = max(1., middle*3+.1)
    inliers = [anchor for anchor,error in zip(anchors,errors) if error <= inlier_limit]
    if len(inliers) >= 6:
        offset = median(y-scale*x for x,y,_ in inliers)
        errors = [abs(y-scale*x-offset) for x,y,_ in anchors]
        middle = median(errors)
    result.update(median_error_seconds=round(middle,6), offset_seconds=round(offset,6),
                  scale=round(scale,9))
    if (middle > .4 or _percentile(errors,.9) > .85 or len(inliers) < .8*len(anchors)):
        result['reason'] = ('scale_outside_supported_range' if not .97 <= slope <= 1.03
                            else 'nonuniform_timing_mismatch')
        return result
    # An isolated, wrong chorus match at either end must not make a cluster of
    # otherwise good anchors appear to cover the complete recording.
    inlier_limit = max(1., middle*3+.1)
    inliers = [anchor for anchor,error in zip(anchors,errors) if error <= inlier_limit]
    if (len(inliers) < 6
            or inliers[-1][0]-inliers[0][0] < reference_span*.72
            or inliers[-1][1]-inliers[0][1] < observed_span*.72):
        result['reason'] = 'insufficient_inlier_timeline_coverage'
        return result
    if abs(offset) > 30.:
        result['reason'] = 'excessive_offset'
        return result
    strong_indices = {row['raw_index'] for row in reference}
    adjusted = []
    for index,cue in enumerate(raw):
        at = scale*cue['at']+offset
        if index in strong_indices and (at < -.5 or at > duration+.5):
            result['reason'] = 'reference_exceeds_recording'
            return result
        # A title/credit cue may precede recording zero; blank end cues can sit
        # beyond a differently padded file. They carry no sung-word evidence.
        at = min(duration,max(0.,at))
        maximum_tick = int(math.floor(duration*100+1e-7))
        at = min(maximum_tick,int(math.floor(at*100+.5)))/100
        adjusted.append({'at':at,'text':cue['text']})
    result.update(cues=adjusted, accepted=True,
                  reason='matched_near_unity_scale' if scale != 1. else 'matched_fixed_offset')
    return result
