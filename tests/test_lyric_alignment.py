"""Offline ASR-to-LRC grouping preserves recording times and recognized text."""
import math
import unittest

from src.lyric_alignment import build_lyric_cues, to_lrc
from src.lyrics import parse_lrc


def word(start, end, text, probability=.95):
    return {'start': start, 'end': end, 'word': text, 'probability': probability}


def segment(words, **extra):
    return {'start': words[0]['start'], 'end': words[-1]['end'], 'words': words,
            'text': ''.join(item['word'] for item in words), **extra}


class LyricAlignmentTests(unittest.TestCase):
    def test_original_recording_times_and_real_gaps_survive_lrc_roundtrip(self):
        result = build_lyric_cues([
            segment([word(12.5, 13.2, '云朵'), word(13.25, 14.1, '在眨眼。')]),
            segment([word(20., 21.4, '月亮偷偷笑。')]),
        ], 30.)
        self.assertEqual(result['cues'], [
            {'at': 12.5, 'text': '云朵在眨眼。'}, {'at': 14.25, 'text': ''},
            {'at': 20., 'text': '月亮偷偷笑。'}, {'at': 21.55, 'text': ''}])
        self.assertFalse(result['warnings'])
        parsed = parse_lrc(to_lrc(result['cues'], '云朵测试'))
        self.assertEqual(parsed['cues'], result['cues'])
        self.assertEqual(parsed['offset_seconds'], 0)

    def test_repeated_chorus_at_different_times_is_preserved(self):
        result = build_lyric_cues([
            segment([word(1., 3., '星星排队回家。')]),
            segment([word(10., 12., '星星排队回家。')]),
        ], 14.)
        self.assertEqual([cue for cue in result['cues'] if cue['text']], [
            {'at': 1., 'text': '星星排队回家。'}, {'at': 10., 'text': '星星排队回家。'}])

    def test_short_lines_use_real_word_boundaries_and_punctuation(self):
        result = build_lyric_cues([segment([
            word(1., 1.5, '小猫'), word(1.5, 2., '拿着'), word(2., 2.5, '小伞，'),
            word(2.6, 3., '小龙'), word(3., 3.5, '背着'), word(3.5, 4., '星光。'),
        ])], 6., max_chars=8)
        self.assertEqual([line['text'] for line in result['lines']], ['小猫拿着小伞，', '小龙背着星光。'])
        self.assertEqual([line['start'] for line in result['lines']], [1., 2.6])
        self.assertEqual([cue['text'] for cue in result['cues']], ['小猫拿着小伞，', '小龙背着星光。', ''])

    def test_long_30_second_segment_is_split_using_actual_word_times(self):
        words = [word(1. + i * 1.5, 2. + i * 1.5, f'字{i}') for i in range(18)]
        result = build_lyric_cues([segment(words)], 30., max_chars=12)
        self.assertGreater(len(result['lines']), 3)
        self.assertEqual(''.join(line['text'] for line in result['lines']), ''.join(item['word'] for item in words))
        starts = {item['start'] for item in words}
        self.assertTrue(all(line['start'] in starts for line in result['lines']))
        self.assertTrue(all(line['end'] - line['start'] <= 8 for line in result['lines']))

    def test_indivisible_token_is_not_given_fabricated_character_timestamps(self):
        text = '云朵口袋里藏着一整条星光做的围巾'
        result = build_lyric_cues([segment([word(2., 11., text)])], 12., max_chars=8)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['text'], text)
        self.assertIn('indivisible_long_token', result['lines'][0]['review_reasons'])
        self.assertIn('coarse_or_long_timing', result['lines'][0]['review_reasons'])

    def test_segment_without_word_times_is_coarse_and_flagged(self):
        result = build_lyric_cues([{'start': 4., 'end': 7., 'text': '小鲸在等晚风', 'avg_logprob': -.4}], 8.)
        line = result['lines'][0]
        self.assertEqual((line['start'], line['end'], line['text']), (4., 7., '小鲸在等晚风'))
        self.assertTrue(line['review_required'])
        self.assertAlmostEqual(line['confidence'], math.exp(-.4), places=5)
        self.assertIn('missing_word_timestamps', line['review_reasons'])

    def test_low_confidence_and_no_speech_flags_are_not_hidden(self):
        result = build_lyric_cues([segment([word(1., 2., '风在撒娇', .3)], no_speech_prob=.8)], 3.)
        self.assertTrue(result['lines'][0]['review_required'])
        self.assertEqual(result['lines'][0]['confidence'], .3)
        self.assertIn('low_confidence', result['lines'][0]['review_reasons'])
        self.assertIn('high_no_speech_probability', result['lines'][0]['review_reasons'])

    def test_english_leading_spaces_and_chinese_word_spaces_are_preserved_appropriately(self):
        result = build_lyric_cues([segment([
            word(1., 1.2, ' bright'), word(1.2, 1.5, ' night'), word(1.5, 1.7, ' 星'), word(1.7, 2., ' 星'),
        ])], 3.)
        self.assertEqual(result['lines'][0]['text'], 'bright night星星')

    def test_no_clear_can_erase_a_next_line_at_the_same_timestamp(self):
        result = build_lyric_cues([
            segment([word(1., 1.99, '小龙笑了。')]),
            segment([word(2., 2.99, '小鲸也笑。')]),
        ], 3., gap_seconds=.01)
        cues = result['cues']
        self.assertEqual([cue for cue in cues if cue['at'] == 2.], [{'at': 2., 'text': '小鲸也笑。'}])
        self.assertTrue(all(cue['at'] <= 3. for cue in cues))

    def test_same_tick_onsets_merge_without_inventing_a_later_time(self):
        result = build_lyric_cues([
            segment([word(1., 1., '小龙。')]),
            segment([word(1.001, 2., '小鲸。')]),
        ], 3.)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual([cue for cue in result['cues'] if cue['text']], [{'at': 1., 'text': '小龙。小鲸。'}])
        self.assertIn('coincident_onsets', result['lines'][0]['review_reasons'])

    def test_final_clear_is_bounded_by_a_fractional_recording_duration(self):
        result = build_lyric_cues([segment([word(.1, 1.006, '晚安。')])], 1.006)
        self.assertEqual(result['cues'][-1], {'at': 1., 'text': ''})
        self.assertLessEqual(result['cues'][-1]['at'], 1.006)

    def test_submillisecond_onsets_do_not_become_nonchronological_after_rounding(self):
        result = build_lyric_cues([
            segment([word(.0006, .0006, '小龙。')]),
            segment([word(.0007, 1.0066, '小鲸。')]),
        ], 1.0066)
        self.assertEqual(result['cues'][0]['at'], 0)
        self.assertEqual(len(result['lines']), 1)
        self.assertLessEqual(result['lines'][0]['end'], 1.0066)

    def test_line_onsets_are_quantized_once_not_twice(self):
        result = build_lyric_cues([
            segment([word(1.0046, 1.0046, '小龙。')]),
            segment([word(1.0047, 2., '小鲸。')]),
        ], 3.)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['cues'][0]['at'], 1.)

    def test_partial_unknown_confidence_requires_review(self):
        result = build_lyric_cues([segment([
            word(1., 1.5, '小龙'), word(1.5, 2., '吃云朵', None),
        ])], 3.)
        self.assertTrue(result['lines'][0]['review_required'])
        self.assertIn('unknown_word_confidence', result['lines'][0]['review_reasons'])

    def test_invalid_times_and_malformed_data_are_rejected(self):
        for item in (word(-.1, 1., '云'), word(2., 1., '云'), word(1., 8., '云'),
                     word(float('nan'), 1., '云'), word(1., float('inf'), '云'),
                     word(True, 1., '云'), word(1., 2., '云', 2.)):
            with self.subTest(item=item), self.assertRaises(ValueError):
                build_lyric_cues([segment([item])], 5.)
        with self.assertRaises(ValueError):
            build_lyric_cues([segment([word(2., 2.5, '小龙'), word(1., 1.5, '小鲸')])], 4.)
        with self.assertRaises(ValueError):
            build_lyric_cues([], 4.)

    def test_serializer_carries_fractional_seconds_and_blocks_lrc_injection(self):
        serialized = to_lrc([{'at': 59.999, 'text': '小龙晚安'}, {'at': 60.2, 'text': ''}], '夜空')
        self.assertIn('[01:00.00]小龙晚安', serialized)
        for text in ('[00:02.00]伪造时间', '[offset:1000]伪造偏移'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                to_lrc([{'at': 1., 'text': text}], '夜空')


if __name__ == '__main__':
    unittest.main()
