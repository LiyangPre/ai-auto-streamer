import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import lameenc

from scripts.import_prepared_songs import import_prepared, read_audio, ensure_studio_stopped
import scripts.import_prepared_songs as song_importer


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

    def run_import(self, **kwargs):
        return import_prepared(self.manifest, self.library, self.reference_meta, **kwargs)

    def old_take_then_new_version(self):
        self.data['tracks'][0]['title'] = '海屿你'
        self.save_manifest()
        old = self.run_import()['imported'][0]['song_id']
        metadata = self.library / (old + '.json')
        item = json.loads(metadata.read_text(encoding='utf-8'))
        item.update(lyrics_cues=[{'at': .1, 'text': 'Old recording fixture'}], lyrics_offset=-.2)
        metadata.write_text(json.dumps(item), encoding='utf-8')
        original = {path.name: path.read_bytes() for path in self.library.glob(old + '*')}
        self.data['tracks'][0]['bvid'] = 'BV14GovBnESm'
        self.save_manifest()
        return old, original

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

    def test_named_replacement_backs_up_before_changing_and_preserves_other_songs_and_policy(self):
        old, original = self.old_take_then_new_version()
        # A same-title take in another profile is deliberately outside replacement.
        other_id = 'b' * 32
        other = json.loads((self.library / (old + '.json')).read_text(encoding='utf-8'))
        other.update(id=other_id, profile_id='other', url='/api/singing/audio/' + other_id,
                     guide_url='/api/singing/audio/' + other_id + '/guide')
        (self.library / (other_id + '.json')).write_text(json.dumps(other), encoding='utf-8')
        (self.library / (other_id + '.mp3')).write_bytes(self.mix.read_bytes())
        (self.library / (other_id + '-guide.mp3')).write_bytes(self.guide.read_bytes())
        other_before = {path.name: path.read_bytes() for path in self.library.glob(other_id + '*')}
        policy = self.root / 'song-requests.json'
        policy.write_text('{"blocked_titles":["告白气球","海屿你","大鱼"]}', encoding='utf-8')
        policy_before = policy.read_bytes()

        result = self.run_import(replace_titles=['海屿你'])
        self.assertFalse(result['playback_started'])
        self.assertEqual(result['replaced'][0]['song_id'], old)
        new_id = result['imported'][0]['song_id']
        new = json.loads((self.library / (new_id + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(new['conversion_provenance']['bvid'], 'BV14GovBnESm')
        self.assertNotIn('lyrics_cues', new)
        self.assertNotIn('lyrics_offset', new)
        self.assertEqual(len(list(self.library.glob('*.json'))), 2)
        self.assertFalse((self.library / (old + '.json')).exists())
        archive = Path(result['replacement_archive'])
        for name, content in original.items():
            self.assertEqual((archive / name).read_bytes(), content)
        with zipfile.ZipFile(result['pre_replacement_backup']['path']) as backup:
            for name, content in original.items():
                self.assertEqual(backup.read('songs/' + name), content)
            self.assertNotIn('songs/' + new_id + '.json', backup.namelist())
        with zipfile.ZipFile(result['backup']['path']) as backup:
            self.assertIn('songs/' + new_id + '.json', backup.namelist())
            self.assertNotIn('songs/' + old + '.json', backup.namelist())
        self.assertEqual(policy.read_bytes(), policy_before)
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(other_id + '*')}, other_before)
        self.assertEqual(json.loads((archive / 'replacement.json').read_text(encoding='utf-8'))['state'], 'complete')

    def test_default_same_title_import_remains_append_only(self):
        old, _ = self.old_take_then_new_version()
        result = self.run_import()
        self.assertEqual(len(result['imported']), 1)
        self.assertNotIn('replaced', result)
        self.assertTrue((self.library / (old + '.json')).is_file())
        self.assertEqual(len(list(self.library.glob('*.json'))), 2)

    def test_exact_version_replacement_is_idempotent_and_keeps_its_existing_lyrics(self):
        self.old_take_then_new_version()
        first = self.run_import(replace_titles=['海屿你'])
        new_id = first['imported'][0]['song_id']
        metadata = self.library / (new_id + '.json')
        new = json.loads(metadata.read_text(encoding='utf-8'))
        new['lyrics_cues'] = [{'at': .2, 'text': 'New recording fixture'}]
        metadata.write_text(json.dumps(new), encoding='utf-8')
        before = metadata.read_bytes()
        second = self.run_import(replace_titles=['海屿你'])
        self.assertEqual(second['imported'], [])
        self.assertEqual(second['replaced'], [])
        self.assertEqual(second['skipped'][0]['song_id'], new_id)
        self.assertIsNone(second['backup'])
        self.assertIsNone(second['pre_replacement_backup'])
        self.assertEqual(metadata.read_bytes(), before)

    def test_replacement_validates_complete_incoming_batch_before_backup_or_publication(self):
        old, original = self.old_take_then_new_version()
        self.data['tracks'].append({**self.data['tracks'][0], 'bvid': 'BV1y8411i7Dd',
                                    'title': '泡沫', 'vocals_mp3': str(self.root / 'missing.mp3')})
        self.save_manifest()
        with patch('scripts.import_prepared_songs.library_backup') as backup:
            with self.assertRaises(FileNotFoundError):
                self.run_import(replace_titles=['海屿你'])
            backup.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_pre_backup_failure_leaves_old_library_untouched(self):
        old, original = self.old_take_then_new_version()
        with patch('scripts.import_prepared_songs.library_backup', side_effect=OSError('backup fixture')):
            with self.assertRaises(OSError):
                self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_refuses_a_readable_backup_with_wrong_song_bytes(self):
        old, original = self.old_take_then_new_version()
        real_backup = song_importer.SingingStudio.backup
        def corrupt_backup(studio, profile_id):
            result = real_backup(studio, profile_id)
            path = studio.root.with_name(studio.root.name + '-backups') / profile_id / (result['id'] + '.zip')
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            entries['songs/' + old + '.mp3'] = b'wrong but CRC-valid backup fixture'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, content in entries.items():
                    archive.writestr(name, content)
            return result
        with patch.object(song_importer.SingingStudio, 'backup', new=corrupt_backup):
            with self.assertRaisesRegex(ValueError, 'differs from the current library'):
                self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_post_backup_failure_rolls_back_published_and_retired_files(self):
        old, original = self.old_take_then_new_version()
        real_backup = song_importer.library_backup
        calls = []
        def backup(studio, profile_id, **kwargs):
            calls.append(profile_id)
            if len(calls) == 2:
                raise OSError('post-backup fixture')
            return real_backup(studio, profile_id, **kwargs)
        with patch('scripts.import_prepared_songs.library_backup', side_effect=backup):
            with self.assertRaises(OSError):
                self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)
        receipts = list((self.root / 'songs-replaced' / 'astra').glob('*/replacement.json'))
        self.assertEqual(json.loads(receipts[0].read_text(encoding='utf-8'))['state'], 'rolled-back')

    def test_replacement_partial_batch_copy_failure_removes_its_new_takes_only(self):
        old, original = self.old_take_then_new_version()
        self.data['tracks'].append({**self.data['tracks'][0], 'bvid': 'BV1y8411i7Dd', 'title': '泡沫'})
        self.save_manifest()
        real_publish = song_importer.publish_track
        calls = []
        def publish(*args):
            calls.append(args[1]['title'])
            if len(calls) == 2:
                raise OSError('second take fixture')
            return real_publish(*args)
        with patch('scripts.import_prepared_songs.publish_track', side_effect=publish):
            with self.assertRaises(OSError):
                self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_quarantine_failure_restores_old_metadata_and_audio(self):
        old, original = self.old_take_then_new_version()
        real_replace = Path.replace
        def replace(source, target):
            if source.name == old + '.mp3' and 'songs-replaced' in str(target):
                raise OSError('quarantine fixture')
            return real_replace(source, target)
        with patch('pathlib.Path.replace', new=replace):
            with self.assertRaises(OSError):
                self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.glob(old + '*')}, original)
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_names_are_explicit_and_unambiguous(self):
        self.old_take_then_new_version()
        with self.assertRaisesRegex(ValueError, 'Every replacement title'):
            self.run_import(replace_titles=['Missing song'])
        self.data['tracks'].append({**self.data['tracks'][0], 'bvid': 'BV1y8411i7Dd', 'title': '海 屿 你'})
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.run_import(replace_titles=['海屿你'])
        self.assertEqual(len(list(self.library.glob('*.json'))), 1)

    def test_replacement_counts_final_capacity_and_reserves_both_backup_slots(self):
        old, _ = self.old_take_then_new_version()
        with patch('scripts.import_prepared_songs.MAX_SONGS_PER_CHARACTER', 1):
            result = self.run_import(replace_titles=['海屿你'])
        self.assertEqual(len(result['imported']), 1)
        self.assertFalse((self.library / (old + '.json')).exists())
        # Three archives now exist: original import, before replacement, after.
        backups = self.root / 'songs-backups' / 'astra'
        for index in range(4):
            (backups / (str(index) + '.zip')).write_bytes(b'backup-count fixture')
        self.data['tracks'][0]['bvid'] = 'BV1y8411i7Dd'
        self.save_manifest()
        before = {path.name: path.read_bytes() for path in self.library.iterdir()}
        with self.assertRaisesRegex(ValueError, 'two free backup slots'):
            self.run_import(replace_titles=['海屿你'])
        self.assertEqual({path.name: path.read_bytes() for path in self.library.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
