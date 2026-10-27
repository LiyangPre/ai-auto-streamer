from pathlib import Path
import tempfile
import unittest

from scripts.seed_vc_batch import excerpt_partial_path, song_lease, worker_paths
try:
    import filelock
    HAS_FILELOCK = True
except ImportError:
    HAS_FILELOCK = False


class SVCShardTests(unittest.TestCase):
    def test_worker_paths_keep_main_compatibility_and_separate_named_shards(self):
        root = Path('fixture')
        main_state, main_lock = worker_paths('main', root)
        self.assertEqual(main_state, root / 'conversion-state.json')
        self.assertEqual(main_lock, root / '.seed-vc.lock')
        first_state, first_lock = worker_paths('a', root)
        second_state, second_lock = worker_paths('b', root)
        self.assertEqual(first_state.name, 'conversion-a-state.json')
        self.assertNotEqual(first_state, second_state)
        self.assertNotEqual(first_lock, second_lock)
        for invalid in ('../escape', 'a/b', 'a\\b', '', 'x' * 33):
            with self.assertRaises(ValueError):
                worker_paths(invalid, root)

    def test_reference_temps_are_per_worker_without_changing_shared_excerpt_name(self):
        target = Path('reference.wav')
        first = excerpt_partial_path(target, 'a')
        second = excerpt_partial_path(target, 'b')
        self.assertNotEqual(first, second)
        self.assertTrue(first.name.startswith(target.name + '.a.'))
        self.assertEqual(first.parent, target.parent)

    @unittest.skipUnless(HAS_FILELOCK, 'Optional isolated conversion environment supplies filelock')
    def test_song_lease_blocks_overlap_and_releases_on_error_or_cached_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with song_lease(directory) as first:
                self.assertTrue(first)
                with song_lease(directory) as overlap:
                    self.assertFalse(overlap)
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with song_lease(directory) as acquired:
                    self.assertTrue(acquired)
                    raise RuntimeError('fixture')
            for _ in range(2):
                with song_lease(directory) as acquired:
                    self.assertTrue(acquired)
                    continue
            with song_lease(directory) as acquired:
                self.assertTrue(acquired)


if __name__ == '__main__':
    unittest.main()
