"""Final-import recovery only: temporary metadata, no audio or provider work."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import finalize_song_batch as finalizer


class FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.jobs = {'tracks': [{'id': 'BV0000000001'}], 'reference_sha256': 'a' * 64,
                     'reference_metadata': str(self.output / 'reference.json'), 'diffusion_steps': 50}
        self.write('jobs.json', self.jobs)
        self.write('reference.json', {})
        self.write('prepared.json', {'ready': 1, 'total': 1, 'reference_sha256': 'a' * 64,
            'tracks': [{'bvid': 'BV0000000001', 'ready': True, 'conversion': {'diffusion_steps': 50}}]})
        for name, value in [('OUTPUT', self.output), ('STATE', self.output / 'pipeline-state.json')]:
            p = patch.object(finalizer.coordinator, name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(finalizer.coordinator, 'assert_voice')
        p.start(); self.addCleanup(p.stop)

    def write(self, name, value):
        (self.output / name).write_text(json.dumps(value), encoding='utf-8')

    def test_healthy_preparation_only_waits_without_importing_or_new_work(self):
        self.write('pipeline-state.json', {'state': 'preparing', 'stage': 'converting'})
        with patch.object(finalizer.coordinator, 'import_library') as importer:
            status = finalizer.step(self.jobs)
        self.assertEqual(status['state'], 'waiting_for_coordinator')
        importer.assert_not_called()

    def test_changed_batch_stop_and_nonbusy_errors_never_restart_or_import(self):
        cases = [({'state': 'error', 'stage': 'converting', 'message': 'worker failed'}, False),
                 ({'state': 'stopped', 'stage': 'importing'}, False),
                 ({'state': 'error', 'stage': 'importing', 'message': 'Library import API returned HTTP 409'}, True)]
        with patch.object(finalizer.coordinator, 'import_library') as importer:
            for state, stop in cases:
                self.write('pipeline-state.json', state)
                if stop: (self.output / 'pipeline.stop').write_text('stop')
                with self.assertRaises(finalizer.FinalizationBlocked):
                    finalizer.step(self.jobs)
                (self.output / 'pipeline.stop').unlink(missing_ok=True)
            self.write('jobs.json', {**self.jobs, 'diffusion_steps': 30})
            with self.assertRaises(finalizer.FinalizationBlocked): finalizer.step(self.jobs)
        importer.assert_not_called()

    def test_busy_timeout_recovers_ready_exports_after_lease_release_and_no_playback(self):
        self.write('pipeline-state.json', {'state': 'error', 'stage': 'importing',
            'message': 'Library import API returned HTTP 409'})
        result = {'imported': [{'bvid': 'BV0000000001'}], 'skipped': [],
                  'reference_sha256': 'a' * 64, 'playback_started': False}
        with patch.object(finalizer.coordinator, 'exclusive_job', return_value=nullcontext()), \
                patch.object(finalizer.coordinator, 'import_library', return_value=result) as importer, \
                patch.object(finalizer.coordinator, 'record_request_status') as requests:
            status = finalizer.step(self.jobs)
        self.assertEqual(status, {'state': 'complete', 'recovered': True, 'playback_started': False})
        importer.assert_called_once()
        requests.assert_called_once()
        self.assertEqual(json.loads((self.output / 'pipeline-state.json').read_text())['state'], 'complete')
        with patch.object(finalizer.coordinator, 'import_library') as importer:
            self.assertEqual(finalizer.step(self.jobs)['state'], 'complete')
        importer.assert_not_called()

    def test_old_receipt_and_partial_exports_do_not_claim_complete(self):
        self.write('pipeline-state.json', {'state': 'complete'})
        self.write('import-result.json', {'imported': [], 'skipped': [], 'playback_started': False})
        with self.assertRaises(finalizer.FinalizationBlocked): finalizer.step(self.jobs)
        self.write('prepared.json', {'ready': 0, 'total': 1, 'tracks': []})
        with self.assertRaises(finalizer.FinalizationBlocked): finalizer.step(self.jobs)

    def test_held_lease_is_waited_on_but_import_permissions_are_not_hidden(self):
        self.write('pipeline-state.json', {'state': 'error', 'stage': 'importing',
            'message': 'Library import API returned HTTP 409'})
        with patch.object(finalizer.coordinator, 'exclusive_job', side_effect=PermissionError()):
            self.assertEqual(finalizer.step(self.jobs)['state'], 'waiting_for_coordinator_cleanup')
        with patch.object(finalizer.coordinator, 'exclusive_job', return_value=nullcontext()), \
                patch.object(finalizer.coordinator, 'import_library', side_effect=PermissionError()):
            with self.assertRaises(PermissionError): finalizer.step(self.jobs)


if __name__ == '__main__':
    unittest.main()
