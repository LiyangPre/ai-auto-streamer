import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

try:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from export_converted_songs import RATE, align_and_level, export_track
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Optional local song-preparation dependencies are not installed')
class SongExportTests(unittest.TestCase):
    def test_instrumental_gaps_close_the_guide_without_changing_voice_timing(self):
        samples = np.arange(RATE) / RATE
        tone = .1 * np.sin(2 * np.pi * 220 * samples)
        original = np.concatenate([np.zeros(RATE), tone, np.zeros(RATE)])
        converted = np.concatenate([np.full(RATE, .005), tone / 2, np.full(RATE, .005)])
        result, _ = align_and_level(original, converted)
        self.assertEqual(len(result), len(original))
        self.assertLess(np.max(np.abs(result[:RATE // 2])), 1e-7)
        self.assertGreater(np.sqrt(np.mean(result[RATE:2 * RATE] ** 2)), .03)
        self.assertLess(np.max(np.abs(result[-RATE // 2:])), 1e-4)

    def test_missing_audio_is_rejected_instead_of_padding_a_missing_verse(self):
        with self.assertRaisesRegex(ValueError, 'drift'):
            align_and_level(np.ones(RATE * 3), np.ones(RATE))

    def test_mix_and_guide_export_aligned_and_reuse_the_same_hashed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            def write(name, tone):
                with wave.open(str(directory / name), 'wb') as target:
                    target.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                    target.writeframes((tone * 32767).astype('<i2').tobytes())
            timeline = np.arange(RATE * 2) / RATE
            write('vocals.wav', .1 * np.sin(timeline * 2 * np.pi * 220))
            write('converted-vocals.wav', .08 * np.sin(timeline * 2 * np.pi * 220))
            write('accompaniment.wav', .04 * np.sin(timeline * 2 * np.pi * 440))
            (directory / 'conversion.json').write_text(json.dumps({'status': 'ready', 'reference_sha256': 'a' * 64, 'steps': 30}))
            track = {'id': 'BV1D64y1Z7Ko', 'bvid': 'BV1D64y1Z7Ko', 'title': 'Fixture',
                     'source_url': 'https://www.bilibili.com/video/BV1D64y1Z7Ko/',
                     'output_dir': str(directory), 'vocals': str(directory / 'vocals.wav'),
                     'accompaniment': str(directory / 'accompaniment.wav')}
            result = export_track(track, {'reference_sha256': 'a' * 64})
            self.assertTrue(result['ready'])
            self.assertAlmostEqual(result['duration_seconds'], 2, delta=.1)
            stamp = Path(result['mix_mp3']).stat().st_mtime_ns
            repeated = export_track(track, {'reference_sha256': 'a' * 64})
            self.assertEqual(repeated['mix_sha256'], result['mix_sha256'])
            self.assertEqual(Path(result['mix_mp3']).stat().st_mtime_ns, stamp)
            self.assertIsNone(export_track(track, {'reference_sha256': 'a' * 64}, required_steps=50))
            track['source_duration'] = 220.
            with self.assertRaisesRegex(ValueError, 'complete source song'):
                export_track(track, {'reference_sha256': 'a' * 64})


if __name__ == '__main__':
    unittest.main()
