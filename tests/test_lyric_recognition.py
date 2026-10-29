"""Offline lyric recognition honors language and reuses only matching caches."""
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts.prepare_song_lyrics import OPTIONS, recognize


@dataclass
class Segment:
    end: float = 12.
    text: str = 'Synthetic test line'


class LyricRecognitionTests(unittest.TestCase):
    def test_english_options_and_sample_limit_reach_recognizer_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Mock()
            model.transcribe.return_value = (iter([Segment()]), SimpleNamespace(language='en'))
            options = {**OPTIONS, 'language':'en'}
            target = Path(folder)/'cache.json'
            result = recognize(model, Path(folder)/'vocals.wav', target, 15., 'english', options=options)
            supplied = model.transcribe.call_args.kwargs
            self.assertEqual(supplied['language'], 'en')
            self.assertEqual(supplied['clip_timestamps'], [0, 15.])
            self.assertTrue(supplied['word_timestamps'])
            self.assertNotIn('clip_timestamps', options)
            self.assertEqual(OPTIONS['language'], 'zh')
            self.assertTrue(result['complete'])
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['language'], 'en')
            model.transcribe.reset_mock()
            self.assertEqual(recognize(model, 'unused', target, 15., 'english', options=options), result)
            model.transcribe.assert_not_called()

    def test_changed_signature_rejects_completed_cache_and_allows_auto_language(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'cache.json'
            target.write_text(json.dumps({'signature':'old-chinese', 'complete':True}), encoding='utf-8')
            model = Mock()
            model.transcribe.return_value = (iter([Segment()]), SimpleNamespace(language='en'))
            result = recognize(model, 'local-vocals.wav', target, 0, 'new-auto', options={**OPTIONS, 'language':None, 'vad_filter':False})
            self.assertIsNone(model.transcribe.call_args.kwargs['language'])
            self.assertFalse(model.transcribe.call_args.kwargs['vad_filter'])
            self.assertNotIn('clip_timestamps', model.transcribe.call_args.kwargs)
            self.assertEqual(result['signature'], 'new-auto')
            self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
