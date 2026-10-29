"""Publication races and Windows sharing failures must not lose song work."""
from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.atomic_json import write_json_atomic
from scripts import atomic_json


def windows_error(code):
    error = PermissionError(errno.EACCES, 'injected sharing violation')
    error.winerror = code
    return error


class AtomicSongStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'pipeline-state.json'
        self.prior = {'state': 'converting', 'completed_chunks': 9, 'voice': 'unchanged'}
        self.path.write_text(json.dumps(self.prior), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_transient_windows_failures_retry_a_complete_unique_file(self):
        real_replace = os.replace
        sources = []

        def replace(source, target):
            self.assertEqual(self.read(), self.prior)
            self.assertEqual(json.loads(Path(source).read_text(encoding='utf-8')), {'completed_chunks': 10})
            sources.append(Path(source))
            if len(sources) < 4:
                raise windows_error([5, 32, 33][len(sources)-1])
            real_replace(source, target)

        with patch.object(atomic_json.os, 'replace', side_effect=replace), patch.object(atomic_json.time, 'sleep') as sleep:
            self.assertTrue(write_json_atomic(self.path, {'completed_chunks': 10}, attempts=4))
        self.assertEqual(len(sources), 4)
        self.assertEqual(len(set(sources)), 1, 'retry the same already complete file')
        self.assertNotEqual(sources[0], self.path.with_suffix('.json.tmp'))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [.05, .1, .15000000000000002])
        self.assertEqual(self.read(), {'completed_chunks': 10})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_exhausted_metadata_retries_raise_without_removing_previous_state(self):
        with patch.object(atomic_json.os, 'replace', side_effect=windows_error(32)) as replace:
            with patch.object(atomic_json.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    write_json_atomic(self.path, {'state': 'complete'}, attempts=3)
        self.assertEqual(replace.call_count, 3)
        self.assertEqual(self.read(), self.prior)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_a_partial_write_is_discarded_before_retrying_with_a_fresh_sibling(self):
        real_fdopen = os.fdopen
        opened = []

        class PartialWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, text):
                self.stream.write(text[:7])
                raise windows_error(5)

        def open_file(descriptor, *args, **kwargs):
            stream = real_fdopen(descriptor, *args, **kwargs)
            opened.append(descriptor)
            return PartialWriter(stream) if len(opened) == 1 else stream

        with patch.object(atomic_json.os, 'fdopen', side_effect=open_file), patch.object(atomic_json.time, 'sleep'):
            self.assertTrue(write_json_atomic(self.path, {'title': '大鱼', 'complete': True}, attempts=2))
        self.assertEqual(len(opened), 2)
        self.assertEqual(self.read(), {'title': '大鱼', 'complete': True})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_progress_failure_is_best_effort_and_does_not_stop_following_compute(self):
        chunks_completed = 9
        with patch.object(atomic_json.os, 'replace', side_effect=windows_error(5)):
            with self.assertLogs('scripts.atomic_json', level='WARNING') as captured:
                written = write_json_atomic(self.path, {'private_payload': 'not-for-logs'},
                                            attempts=2, retry_delay=0, best_effort=True)
            chunks_completed += 1
        self.assertFalse(written)
        self.assertEqual(chunks_completed, 10)
        self.assertEqual(self.read(), self.prior)
        self.assertNotIn('private_payload', ''.join(captured.output))
        self.assertNotIn('not-for-logs', ''.join(captured.output))

    def test_nontransient_and_serialization_failures_do_not_retry_or_damage_target(self):
        with patch.object(atomic_json.os, 'replace', side_effect=OSError(errno.ENOSPC, 'disk full')) as replace:
            with patch.object(atomic_json.time, 'sleep') as sleep:
                with self.assertRaises(OSError):
                    write_json_atomic(self.path, {'state': 'complete'})
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        for payload in ({'eta': float('nan')}, {'eta': float('inf')}, {'bad': object()}):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                write_json_atomic(self.path, payload, best_effort=True)
        self.assertEqual(self.read(), self.prior)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_concurrent_writers_never_share_temps_or_publish_partial_json(self):
        real_replace = os.replace
        barrier = threading.Barrier(8)
        lock = threading.Lock()
        seen = []

        def replace(source, target):
            source = Path(source)
            data = json.loads(source.read_text(encoding='utf-8'))
            self.assertEqual(data['padding'], 'complete' * 1000)
            with lock:
                first_attempt = source not in seen
                if first_attempt:
                    seen.append(source)
            if first_attempt:
                barrier.wait(timeout=5)
            return real_replace(source, target)

        def writer(index):
            return write_json_atomic(self.path, {'writer': index, 'padding': 'complete' * 1000}, retry_delay=.01)

        with patch.object(atomic_json.os, 'replace', side_effect=replace):
            with ThreadPoolExecutor(max_workers=8) as executor:
                self.assertEqual(list(executor.map(writer, range(8))), [True] * 8)
        self.assertEqual(len(set(seen)), 8)
        self.assertTrue(all(path.parent == self.path.parent for path in seen))
        self.assertIn(self.read()['writer'], range(8))
        self.assertEqual(self.read()['padding'], 'complete' * 1000)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_cleanup_removes_only_the_current_writers_file(self):
        unrelated = self.root / '.pipeline-state.json.other-writer.tmp'
        unrelated.write_text('still being written elsewhere', encoding='utf-8')
        with patch.object(atomic_json.os, 'replace', side_effect=windows_error(33)):
            self.assertFalse(write_json_atomic(self.path, {'new': True}, attempts=1, best_effort=True))
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'still being written elsewhere')
        self.assertEqual(self.read(), self.prior)
        self.assertEqual(list(self.root.glob('*.tmp')), [unrelated])

    def test_all_song_publish_wrappers_use_strict_metadata_by_default(self):
        from scripts import seed_vc_batch, separate_song_sources, queue_astra_song
        from scripts import export_converted_songs
        for module in (seed_vc_batch, separate_song_sources, queue_astra_song, export_converted_songs):
            with self.subTest(module=module.__name__):
                with patch.object(module, 'write_json_atomic', return_value=True) as writer:
                    self.assertTrue(module.publish(self.path, {'ready': True}))
                    writer.assert_called_once_with(self.path, {'ready': True}, best_effort=False)
                with patch.object(module, 'write_json_atomic', return_value=False) as writer:
                    self.assertFalse(module.publish(self.path, {'status': 'converting'}, best_effort=True))
                    writer.assert_called_once_with(self.path, {'status': 'converting'}, best_effort=True)


if __name__ == '__main__':
    unittest.main()
