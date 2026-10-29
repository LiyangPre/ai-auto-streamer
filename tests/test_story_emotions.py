"""Acting tags change bounded facial cues without changing voice or timing."""
import math
import unittest

from src.story_catalog import STORIES
from src.story_emotions import story_expression_cues


class StoryEmotionTests(unittest.TestCase):
    def test_uses_spoken_characters_not_acting_tags_for_time_positions(self):
        cues = story_expression_cues('（得意）甲乙（小声，心虚）丙丁（惊讶）戊己', 12)
        self.assertEqual(cues, [
            {'at': 0., 'emotion': 'neutral'},
            {'at': 0., 'emotion': 'happy'},
            {'at': 4., 'emotion': 'shy'},
            {'at': 8., 'emotion': 'surprised'},
        ])

    def test_unknown_tags_and_punctuation_do_not_shift_or_create_acting_beats(self):
        a = story_expression_cues('甲乙（不改变情绪的备注）（开心）丙丁', 8)
        b = story_expression_cues('甲，乙！（开心）丙 丁。', 8)
        self.assertEqual(a, b)
        self.assertEqual(a[-1], {'at': 4., 'emotion': 'happy'})

    def test_collapses_adjacent_moods_and_limits_long_scripts(self):
        duplicate = story_expression_cues('（开心）甲（得意）乙（温柔）丙', 3)
        self.assertEqual([cue['emotion'] for cue in duplicate], ['neutral', 'happy'])
        long = story_expression_cues('甲（开心）乙（心虚）' * 20, 50)
        self.assertEqual(len(long), 12)
        self.assertTrue(all(left['emotion'] != right['emotion'] for left, right in zip(long, long[1:])))

    def test_rejects_invalid_durations_and_handles_empty_or_tag_only_text(self):
        for value in (float('nan'), float('inf'), float('-inf'), 10 ** 1000, -1, 0, True, '12', None):
            with self.subTest(duration=value):
                self.assertEqual(story_expression_cues('（开心）你好', value), [])
        for text in ('', None, '（得意）', '  '):
            self.assertEqual(story_expression_cues(text, 5), [{'at': 0., 'emotion': 'neutral'}])

    def test_large_finite_duration_does_not_overflow_while_scaling_a_prefix(self):
        cues = story_expression_cues('甲乙（开心）丙丁', 1e308)
        self.assertEqual(cues[-1]['at'], 5e307)
        self.assertTrue(all(math.isfinite(cue['at']) for cue in cues))

    def test_all_ten_stories_have_finite_monotone_cute_emotions_on_the_audio_clock(self):
        allowed = {'neutral', 'happy', 'shy', 'thinking', 'surprised', 'sad'}
        for story in STORIES:
            with self.subTest(story=story['id']):
                cues = story_expression_cues(story['text'], 67.125)
                self.assertGreaterEqual(len(cues), 3)
                self.assertEqual(cues[0], {'at': 0., 'emotion': 'neutral'})
                self.assertEqual([cue['at'] for cue in cues], sorted(cue['at'] for cue in cues))
                for cue in cues:
                    self.assertTrue(math.isfinite(cue['at']))
                    self.assertLessEqual(0, cue['at'])
                    self.assertLessEqual(cue['at'], 67.125)
                    self.assertIn(cue['emotion'], allowed)
