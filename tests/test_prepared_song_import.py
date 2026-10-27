import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import lameenc

from scripts.import_prepared_songs import import_prepared, read_audio, ensure_studio_stopped


def audio(seconds=1):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(24000)
    encoder.set_channels(1)
    return bytes(encoder.encode(b'\0' * int(48000 * seconds)) + encoder.flush())


class PreparedSongImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'songs'
        ref = self.root / 'reference.wav'
        ref.write_bytes(b'approved target fixture')
        self.reference_hash = hashlib.sha256(ref.read_bytes()).hexdigest()
        self.reference_meta = self.root / 'reference.json'
        self.reference_meta.write_text(json.dumps({'path': str(ref), 'approved': True, 'candidate_id': 'B', 'reference_sha256': self.reference_hash}), encoding='utf-8')
        self.mix = self.root / 'mix.mp3'; self.mix.write_bytes(audio())
        self.guide = self.root / 'guide.mp3'; self.guide.write_bytes(audio())
        self.manifest = self.root / 'prepared.json'
        self.data = {'voice_candidate': 'B', 'reference_sha256': self.reference_hash, 'tracks': [
            {'bvid': 'BV1D64y1Z7Ko', 'title': 'Prepared song', 'mix_mp3': str(self.mix), 'vocals_mp3': str(self.guide),
             'duration_seconds': read_audio(self.mix)['duration'], 'conversion': {'engine': 'Seed-VC', 'diffusion_steps': 30}, 'ready': True}]}
        self.save_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.data), encoding='utf-8')

    def run_import(self):
        return import_prepared(self.manifest, self.library, self.reference_meta)

    def test_import_is_idempotent_and_backup_contains_mix_guide_and_provenance(self):
        first = self.run_import()
        self.assertEqual(len(first['imported']), 1)
        self.assertFalse(first['playback_started'])
        song_id = first['imported'][0]['song_id']
        item = json.loads((self.library / (song_id + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(item['source'], 'upload')
        self.assertEqual(item['conversion_provenance']['reference_sha256'], self.reference_hash)
        self.assertEqual(item['conversion_provenance']['settings']['diffusion_steps'], 30)
        self.assertEqual((self.library / (song_id + '-guide.mp3')).read_bytes(), self.guide.read_bytes())
        with zipfile.ZipFile(first['backup']['path']) as backup:
            self.assertIn('songs/' + song_id + '-guide.mp3', backup.namelist())
            stored = json.loads(backup.read('songs/' + song_id + '.json'))
            self.assertEqual(stored['conversion_provenance'], item['conversion_provenance'])
        second = self.run_import()
        self.assertEqual(second['imported'], [])
        self.assertEqual(second['skipped'][0]['song_id'], song_id)
        self.assertIsNone(second['backup'])
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_rejects_duration_or_reference_mismatch_before_publishing_any_track(self):
        self.guide.write_bytes(audio(3))
        with self.assertRaisesRegex(ValueError, 'matching duration'):
            self.run_import()
        self.assertEqual(list(self.library.glob('*')), [])
        self.guide.write_bytes(audio())
        self.data['reference_sha256'] = 'a' * 64; self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'approved voice reference'):
            self.run_import()
        self.assertEqual(list(self.library.glob('*')), [])

    def test_current_profile_reference_is_read_from_manifest_and_must_match_profile(self):
        reference = json.loads(self.reference_meta.read_text(encoding='utf-8'))
        reference.update(candidate_id='current-profile', profile_id='astra', profile_revision=13,
                         name='Astra current saved voice')
        self.reference_meta.write_text(json.dumps(reference), encoding='utf-8')
        self.data.update(voice_candidate='current-profile', reference_metadata=str(self.reference_meta),
                         reference=reference['path'])
        self.save_manifest()
        result = import_prepared(self.manifest, self.library)
        self.assertEqual(result['voice_candidate'], 'current-profile')
        item = json.loads((self.library / (result['imported'][0]['song_id'] + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(item['conversion_provenance']['voice_candidate'], 'current-profile')
        reference['profile_id'] = 'another-character'
        self.reference_meta.write_text(json.dumps(reference), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'matching approved voice'):
            import_prepared(self.manifest, self.library)

    def test_failed_copy_publishes_no_partial_track_and_lock_is_released(self):
        with patch('scripts.import_prepared_songs.shutil.copyfile', side_effect=OSError('disk fixture')):
            with self.assertRaises(OSError):
                self.run_import()
        self.assertEqual(list(self.library.iterdir()), [])
        self.assertEqual(len(self.run_import()['imported']), 1)

    def test_corrupt_mp3_or_incomplete_expected_batch_is_rejected_before_publication(self):
        self.mix.write_bytes(b'not an mp3')
        with self.assertRaisesRegex(ValueError, 'readable MP3'):
            self.run_import()
        self.mix.write_bytes(audio())
        with self.assertRaisesRegex(ValueError, 'complete prepared batch'):
            import_prepared(self.manifest, self.library, self.reference_meta, expected_total=9)
        self.assertEqual(list(self.library.glob('*')), [])

    def test_damaged_existing_guide_is_not_silently_skipped(self):
        first = self.run_import()
        (self.library / (first['imported'][0]['song_id'] + '-guide.mp3')).unlink()
        with self.assertRaisesRegex(ValueError, 'missing or damaged'):
            self.run_import()

    def test_offline_guard_refuses_running_or_unverifiable_controller(self):
        with patch('scripts.import_prepared_songs.socket.create_connection'):
            with self.assertRaisesRegex(ValueError, 'Stop Studio'):
                ensure_studio_stopped('127.0.0.1', 8000)
        with patch('scripts.import_prepared_songs.socket.create_connection', side_effect=PermissionError()):
            with self.assertRaisesRegex(ValueError, 'Could not verify'):
                ensure_studio_stopped('127.0.0.1', 8000)
        with patch('scripts.import_prepared_songs.socket.create_connection', side_effect=ConnectionRefusedError()):
            ensure_studio_stopped('127.0.0.1', 8000)


if __name__ == '__main__':
    unittest.main()
