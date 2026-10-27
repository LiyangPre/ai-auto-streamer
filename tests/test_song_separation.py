import unittest

from scripts.separate_song_sources import validate_audio_duration


class SongAudioDurationTests(unittest.TestCase):
    def test_video_tail_does_not_reject_the_shorter_valid_audio(self):
        # The selected Dao Xiang video is longer than its actual audio stream.
        result = validate_audio_duration(223.48, {'duration': 229.301, 'audio_duration': 223.512})
        self.assertEqual(result['reference'], 'manifest.audio_duration')
        self.assertEqual(result['video_duration_metadata'], 229.301)

    def test_legacy_manifest_uses_job_audio_duration_instead_of_video_runtime(self):
        result = validate_audio_duration(223.48, {'duration': 229.301}, {'source_duration': 223.512})
        self.assertEqual(result['reference'], 'jobs.source_duration')
        self.assertEqual(result['expected_audio_duration'], 223.512)

    def test_video_only_metadata_is_not_an_audio_length_constraint(self):
        result = validate_audio_duration(223.48, {'duration': 229.301})
        self.assertEqual(result['reference'], 'decoded_audio_only')
        self.assertIsNone(result['expected_audio_duration'])

    def test_a_genuinely_truncated_audio_file_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'audio reference'):
            validate_audio_duration(180.0, {'duration': 229.301, 'audio_duration': 223.512})


if __name__ == '__main__':
    unittest.main()
