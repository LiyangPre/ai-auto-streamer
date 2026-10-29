"""Intent policy and bounded voting; no model, provider, speech or disk writes."""
import unittest

from pydantic import ValidationError

from src.audience_intents import (
    AudienceIntentPolicy, AudienceIntentPool, IntentDecision,
    MAX_HISTORY, MAX_PROFILES, MAX_VOTES,
)


class Clock:
    def __init__(self):
        self.value = 1000.

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def decision(intent='singing', **extra):
    return {'intent': intent, 'confidence': .95, **extra}


class AudienceIntentTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pool = AudienceIntentPool(clock=self.clock)
        self.pool.set_active_profile('astra')

    def vote(self, index, intent='singing', profile='astra', **extra):
        return self.pool.record(profile, f'viewer-{index}', f'message-{index}', decision(intent, **extra))

    def demand(self, intent='singing', start=0):
        for i in range(start, start + 6):
            self.assertTrue(self.vote(i, intent)['accepted'])

    def test_models_reject_untrusted_commands_nonfinite_and_coerced_values(self):
        self.assertEqual(AudienceIntentPolicy().threshold, 6)
        self.assertEqual(AudienceIntentPolicy().window_seconds, 120)
        for value in [
            {'intent': 'run_shell', 'confidence': 1},
            {'intent': 'story', 'title': '歌名'},
            {'intent': 'singing', 'title': 'a' * 81},
            {'intent': 'singing', 'title': 'hello\nworld'},
            {'confidence': float('nan')}, {'confidence': float('inf')},
            {'confidence': '1'}, {'positive': 'true'}, {'current_request': 1},
            {'script': 'anything'},
        ]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                IntentDecision.model_validate(value)
        for value in [{'threshold': 1}, {'threshold': '6'}, {'enabled': 'false'},
                      {'window_seconds': float('inf')}, {'minimum_confidence': .1},
                      {'selection_seconds': 400}, {'threshold': 129}]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AudienceIntentPolicy.model_validate(value)
        title = IntentDecision.model_validate(decision(title='  Ｓｏｎｇ '))
        self.assertEqual(title.title, 'Song')

    def test_default_requires_six_positive_votes_not_five(self):
        for i in range(5):
            self.vote(i)
        self.assertIsNone(self.pool.peek_trigger('astra'))
        self.assertEqual(self.pool.public('astra')['counts'], {'singing': 5, 'story': 0})
        self.vote(5, title='起风了')
        trigger = self.pool.peek_trigger('astra')
        self.assertEqual(trigger, {'intent': 'singing', 'votes': 6, 'title': None})
        self.assertEqual(self.pool.peek_trigger('astra'), trigger, 'peeking cannot consume votes')

    def test_negative_quoted_hypothetical_low_confidence_and_invalid_do_not_vote(self):
        examples = [decision(positive=False), decision(current_request=False),
                    decision(confidence=.79), decision('none'),
                    {'intent': 'play_song', 'confidence': 1}, {'confidence': float('nan')}]
        for i, value in enumerate(examples):
            self.assertFalse(self.pool.record('astra', i, str(i), value)['accepted'])
        self.assertEqual(self.pool.public('astra')['counts'], {'singing': 0, 'story': 0})

    def test_message_deduplication_and_shared_viewer_cooldown(self):
        self.assertTrue(self.vote(1)['accepted'])
        duplicate = self.pool.record('astra', 'different-viewer', 'message-1', decision('story'))
        self.assertEqual(duplicate['code'], 'duplicate_message')
        rapid = self.pool.record('astra', 'viewer-1', 'different-message', decision('story'))
        self.assertEqual(rapid['code'], 'viewer_cooldown')
        self.clock.advance(20)
        result = self.pool.record('astra', 'viewer-1', 'later-message', decision('story'))
        self.assertTrue(result['accepted'])
        self.assertEqual(result['counts'], {'singing': 1, 'story': 1})

    def test_busy_or_offline_does_not_consume_pending_demand(self):
        self.demand()
        for conditions in [(False, True), (True, False), (1, True), (True, 1)]:
            self.assertIsNone(self.pool.consume_trigger('astra', stage_free=conditions[0], output_ready=conditions[1]))
            self.assertEqual(self.pool.peek_trigger('astra')['votes'], 6)
        triggered = self.pool.consume_trigger('astra', stage_free=True, output_ready=True)
        self.assertEqual(triggered['intent'], 'singing')
        self.assertEqual(self.pool.public('astra')['counts'], {'singing': 0, 'story': 0})
        self.assertIsNone(self.pool.consume_trigger('astra', stage_free=True, output_ready=True))

    def test_expired_pending_votes_never_trigger_when_stage_later_frees(self):
        self.demand('story')
        self.clock.advance(120)
        self.assertIsNone(self.pool.consume_trigger('astra', stage_free=True, output_ready=True))
        self.assertEqual(self.pool.public('astra')['counts']['story'], 0)

    def test_cooldown_needs_current_votes_and_does_not_preserve_stale_demand(self):
        self.demand()
        self.pool.consume_trigger('astra', stage_free=True, output_ready=True)
        self.demand('story', start=20)
        self.assertIsNone(self.pool.peek_trigger('astra'))
        self.assertEqual(self.pool.public('astra')['cooldown_remaining'], 300)
        self.clock.advance(301)
        self.assertIsNone(self.pool.peek_trigger('astra'), 'votes during cooldown already expired')
        self.demand('story', start=30)
        self.assertEqual(self.pool.peek_trigger('astra')['intent'], 'story')

    def test_largest_current_pool_wins_and_tie_uses_earlier_threshold(self):
        self.demand('story')
        self.clock.advance(1)
        self.demand('singing', start=20)
        self.assertEqual(self.pool.peek_trigger('astra')['intent'], 'story')
        self.vote(26, 'singing')
        self.assertEqual(self.pool.peek_trigger('astra')['intent'], 'singing')
        self.pool.consume_trigger('astra', stage_free=True, output_ready=True)
        self.assertEqual(self.pool.public('astra')['counts'], {'singing': 0, 'story': 0})

    def test_song_solicitation_accepts_implicit_title_but_never_autoplays(self):
        self.assertTrue(self.pool.begin_song_selection('astra'))
        selected = self.vote(1, title='还没学的歌')
        self.assertEqual(selected, {'accepted': True, 'code': 'song_selection',
                                    'intent': 'singing', 'title': '还没学的歌'})
        self.assertTrue(self.pool.song_selection('astra')['awaiting_song'])
        self.assertEqual(self.pool.public('astra')['counts']['singing'], 0)
        self.assertEqual(self.vote(2, 'story')['code'], 'awaiting_song')
        self.assertEqual(self.vote(3)['code'], 'awaiting_song')
        self.pool.complete_song_selection('astra')
        self.assertIsNone(self.pool.song_selection('astra'))

    def test_selection_short_cooldown_allows_correction_without_vote_cooldown(self):
        self.pool.begin_song_selection('astra')
        self.vote(1, title='不存在')
        self.assertEqual(self.pool.record('astra', 'viewer-1', 'other', decision(title='起风了'))['code'],
                         'selection_cooldown')
        self.clock.advance(2)
        selected = self.pool.record('astra', 'viewer-1', 'corrected', decision(title='起风了'))
        self.assertEqual(selected['code'], 'song_selection')

    def test_solicitation_expires_and_repeated_begin_does_not_extend_it(self):
        self.pool.begin_song_selection('astra')
        self.clock.advance(70)
        self.pool.begin_song_selection('astra')
        self.assertEqual(self.pool.song_selection('astra')['remaining_seconds'], 20)
        self.clock.advance(20)
        self.assertIsNone(self.pool.song_selection('astra'))
        self.assertEqual(self.vote(1, title='起风了')['code'], 'vote')

    def test_profile_change_rejects_stale_async_results_and_closes_old_selection(self):
        self.demand()
        self.pool.begin_song_selection('astra')
        self.pool.set_active_profile('claude')
        result = self.vote(20, profile='astra')
        self.assertEqual(result['code'], 'stale_profile')
        self.assertIsNone(self.pool.peek_trigger('astra'))
        self.assertIsNone(self.pool.song_selection('astra'))
        self.assertEqual(self.pool.public('claude')['counts'], {'singing': 0, 'story': 0})
        for i in range(6):
            self.vote(i, 'story', profile='claude')
        self.assertEqual(self.pool.peek_trigger('claude')['intent'], 'story')
        self.assertEqual(self.pool.public('astra')['counts']['singing'], 6)

    def test_disable_clears_pending_work_but_does_not_forget_trigger_cooldown(self):
        self.demand()
        self.pool.consume_trigger('astra', stage_free=True, output_ready=True)
        self.pool.begin_song_selection('astra')
        self.pool.set_policy('astra', {'enabled': False})
        self.assertEqual(self.vote(20)['code'], 'disabled')
        self.assertFalse(self.pool.begin_song_selection('astra'))
        self.assertIsNone(self.pool.song_selection('astra'))
        self.pool.set_policy('astra', {'enabled': True})
        self.assertEqual(self.pool.public('astra')['cooldown_remaining'], 300)

    def test_all_histories_votes_and_profiles_have_hard_limits(self):
        for i in range(MAX_HISTORY + 100):
            self.vote(i)
        state = self.pool._profiles['astra']
        self.assertEqual(len(state.votes), MAX_VOTES)
        self.assertEqual(len(state.messages), MAX_HISTORY)
        self.assertEqual(len(state.viewers), MAX_HISTORY)
        self.pool.begin_song_selection('astra')
        for i in range(MAX_HISTORY + 100, MAX_HISTORY * 2 + 200):
            self.vote(i, title='未解析的歌名')
        self.assertEqual(len(state.selections), MAX_HISTORY)
        for i in range(MAX_PROFILES - 1):
            self.pool.set_active_profile(f'profile-{i}')
        with self.assertRaises(ValueError):
            self.pool.set_active_profile('overflow')
        self.assertEqual(len(self.pool._profiles), MAX_PROFILES)

    def test_public_snapshot_has_no_viewer_identifiers_and_is_independent(self):
        self.pool.record('astra', 'private-user', 'private-message', decision())
        view = self.pool.public('astra')
        self.assertNotIn('private-user', str(view))
        self.assertNotIn('private-message', str(view))
        view['counts']['singing'] = 99
        view['policy']['threshold'] = 2
        self.assertEqual(self.pool.public('astra')['counts']['singing'], 1)
        self.assertEqual(self.pool.public('astra')['policy']['threshold'], 6)

    def test_invalid_identity_rejected_without_poisoning_pool(self):
        for identity in ['', 'x' * 257, None, {}, True]:
            self.assertEqual(self.pool.record('astra', identity, 'id', decision())['code'], 'missing_identity')
        self.assertEqual(self.pool.public('astra')['counts']['singing'], 0)
        with self.assertRaises(ValueError):
            self.pool.set_active_profile('../foreign')

    def test_clear_cancels_selection_and_votes(self):
        self.demand()
        self.pool.begin_song_selection('astra')
        self.pool.clear('astra')
        self.assertIsNone(self.pool.song_selection('astra'))
        self.assertIsNone(self.pool.peek_trigger('astra'))


if __name__ == '__main__':
    unittest.main()
