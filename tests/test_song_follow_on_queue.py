"""Metadata-only follow-on scheduling: no downloads, model loads or playback."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from scripts import queue_astra_song as queued


class FollowOnQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'data/songs-converted'
        self.output.mkdir(parents=True)
        sources = self.root / 'data/song-sources'
        sources.mkdir()
        self.original = ['BV1D64y1Z7Ko', 'BV1y82uB2ET4', 'BV1A3411J7HX', 'BV1f59QBGEhx']
        self.token = 'BV14nrkBPEBE'
        self.reference = self.root / 'reference.wav'
        self.reference.write_bytes(b'approved-reference')
        self.reference_metadata = self.root / 'reference.json'
        self.reference_metadata.write_text('{}')
        self.reference_hash = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        audio = sources / (self.token + '.m4a')
        audio.write_bytes(b'downloaded-original-audio')
        directory = self.output / self.token
        self.track = {'id': self.token, 'bvid': self.token, 'title': '海屿你', 'source': str(audio),
                      'source_sha256': hashlib.sha256(audio.read_bytes()).hexdigest(), 'source_duration': 295.54,
                      'reference': str(self.reference), 'reference_metadata': str(self.reference_metadata),
                      'reference_sha256': self.reference_hash, 'output_dir': str(directory),
                      'vocals': str(directory / 'vocals.wav'), 'accompaniment': str(directory / 'accompaniment.wav')}
        self.jobs = {'version': 1, 'reference': str(self.reference), 'reference_metadata': str(self.reference_metadata),
                     'reference_sha256': self.reference_hash, 'voice_candidate': 'current-profile',
                     'diffusion_steps': 50, 'max_workers': 12,
                     'tracks': [{'id': token, 'bvid': token} for token in self.original]}
        self.source = {'bvid': self.token, 'original_audio': str(audio), 'ok': True, 'audio_duration': 295.54}
        self.request_track = {'title': '海屿你', 'conversion_status': 'queued',
                              'source_recording': {'bvid': self.token, 'original_audio': str(audio)}}
        self.pending = {'version': 1, 'state': 'queued', 'diffusion_steps': 50,
                        'expected_batch_ids': self.original, 'reference_sha256': self.reference_hash,
                        'voice_candidate': 'current-profile', 'track': self.track,
                        'source': self.source, 'request_track': self.request_track}
        self.path = self.output / ('pending-' + self.token + '.json')
        self.publish(self.path, self.pending)
        self.publish(self.output / 'jobs.json', self.jobs)
        self.publish(self.output / 'sources.json', [{'bvid': token} for token in self.original])
        self.publish(self.root / 'data/song-requests.json', {'current_batch_bvids': self.original,
                     'current_batch_total': 4, 'tracks': [{'source_recording': {'bvid': token}} for token in self.original]})
        self.publish(self.output / 'pipeline-state.json', {'state': 'preparing', 'total': 4, 'ready': 2, 'updated': time.time()})
        for patcher in [patch.object(queued, 'ROOT', self.root), patch.object(queued, 'OUTPUT', self.output),
                        patch.object(queued.coordinator, 'ROOT', self.root), patch.object(queued.coordinator, 'OUTPUT', self.output),
                        patch.object(queued.coordinator, 'assert_voice')]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def complete(self, batch_ids=None):
        batch_ids = batch_ids or self.original
        total = len(batch_ids)
        self.publish(self.output / 'pipeline-state.json', {'state': 'complete', 'stage': 'complete',
                     'total': total, 'ready': total, 'diffusion_steps': 50})
        self.publish(self.output / 'prepared.json', {'total': total, 'ready': total,
                     'reference_sha256': self.reference_hash,
                     'tracks': [{'bvid': token, 'ready': True, 'conversion': {'diffusion_steps': 50}}
                                for token in batch_ids]})
        self.publish(self.output / 'import-result.json', {'reference_sha256': self.reference_hash,
                     'imported': [{'bvid': token} for token in batch_ids], 'skipped': [], 'playback_started': False})

    def test_waiting_does_not_edit_running_manifests_or_launch_workers(self):
        before = {path: path.read_bytes() for path in [self.output / 'jobs.json', self.output / 'sources.json',
                                                      self.root / 'data/song-requests.json']}
        with patch.object(queued, 'launch_follow_on') as launch, \
                patch.object(queued.coordinator, 'exclusive_job') as lease, \
                patch.object(queued.subprocess, 'Popen') as process:
            status = queued.run(self.path)
        self.assertEqual(status['state'], 'queued')
        self.assertEqual(status['stage'], 'waiting_for_current_batch')
        launch.assert_not_called()
        lease.assert_not_called()
        process.assert_not_called()
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_incomplete_or_unimported_original_batch_cannot_activate(self):
        jobs_path = self.output / 'jobs.json'
        before = jobs_path.read_bytes()
        with self.assertRaisesRegex(queued.QueueBlocked, 'has not completed'):
            queued.activate(self.pending)
        self.assertEqual(jobs_path.read_bytes(), before)
        self.complete()
        (self.output / 'import-result.json').unlink()
        with self.assertRaisesRegex(queued.QueueBlocked, 'has not completed'):
            queued.activate(self.pending)
        self.assertEqual(jobs_path.read_bytes(), before)

    def test_changed_original_identifiers_block_without_writing_manifests(self):
        self.complete()
        changed = queued.read(self.output / 'jobs.json')
        changed['tracks'][0] = {'id': 'BV0000000001', 'bvid': 'BV0000000001'}
        self.publish(self.output / 'jobs.json', changed)
        paths = [self.output / 'jobs.json', self.output / 'sources.json', self.root / 'data/song-requests.json']
        before = {path: path.read_bytes() for path in paths}
        with self.assertRaisesRegex(queued.QueueBlocked, 'active batch changed'):
            queued.activate(self.pending)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_completion_requires_import_and_exact_quality(self):
        self.complete()
        self.assertTrue(queued.batch_completed(self.original, self.reference_hash))
        result = queued.read(self.output / 'import-result.json')
        result['imported'].pop()
        self.publish(self.output / 'import-result.json', result)
        self.assertFalse(queued.batch_completed(self.original, self.reference_hash))
        self.complete()
        result = queued.read(self.output / 'prepared.json')
        result['tracks'][0]['conversion']['diffusion_steps'] = 30
        self.publish(self.output / 'prepared.json', result)
        self.assertFalse(queued.batch_completed(self.original, self.reference_hash))

    def test_activation_releases_old_lease_before_launch_and_preserves_originals(self):
        self.complete()
        leased = False

        @contextmanager
        def lease():
            nonlocal leased
            self.assertFalse(leased)
            leased = True
            try:
                yield
            finally:
                leased = False

        def launch(pending):
            self.assertFalse(leased)
            return Mock(pid=123, poll=Mock(return_value=None))

        with patch.object(queued.coordinator, 'exclusive_job', lease), \
                patch.object(queued, 'launch_follow_on', side_effect=launch) as launched:
            status = queued.run(self.path)
        self.assertEqual(status['state'], 'activated')
        self.assertEqual(status['coordinator_pid'], 123)
        launched.assert_called_once()
        jobs = queued.read(self.output / 'jobs.json')
        self.assertEqual(jobs['tracks'][:4], self.jobs['tracks'])
        self.assertEqual(jobs['tracks'][-1], self.track)
        self.assertEqual(len(queued.read(self.output / 'sources.json')), 5)
        requests = queued.read(self.root / 'data/song-requests.json')
        self.assertEqual(requests['current_batch_bvids'], self.original + [self.token])
        self.assertEqual(requests['tracks'][-1], self.request_track)
        self.assertFalse(queued.activate(status))  # Idempotent on recovery.

    def test_voice_batch_and_source_changes_block_activation(self):
        self.complete()
        jobs_path = self.output / 'jobs.json'
        changed = {**self.jobs, 'reference_sha256': '0' * 64}
        self.publish(jobs_path, changed)
        with self.assertRaises(queued.QueueBlocked):
            queued.activate(self.pending)
        self.publish(jobs_path, self.jobs)
        Path(self.track['source']).write_bytes(b'changed during wait')
        with self.assertRaisesRegex(queued.QueueBlocked, 'source changed'):
            queued.activate(self.pending)
        self.assertEqual(queued.read(jobs_path), self.jobs)

    def test_interrupted_manifest_transaction_recovers_once(self):
        self.complete()
        self.publish(self.output / 'sources.json', [{'bvid': token} for token in self.original] + [self.source])
        requests = queued.read(self.root / 'data/song-requests.json')
        requests['tracks'].append(self.request_track)
        requests.update(current_batch_bvids=self.original + [self.token], current_batch_total=5)
        self.publish(self.root / 'data/song-requests.json', requests)
        self.assertTrue(queued.activate(self.pending))
        self.assertFalse(queued.activate(self.pending))
        self.assertEqual(len(queued.read(self.output / 'jobs.json')['tracks']), 5)
        self.assertEqual(len(queued.read(self.root / 'data/song-requests.json')['tracks']), 5)

    def test_follow_on_completion_does_not_launch_or_autoplay(self):
        self.complete()
        queued.activate(self.pending)
        self.complete(self.original + [self.token])
        with patch.object(queued, 'launch_follow_on') as launch:
            status = queued.run(self.path)
        self.assertEqual(status['state'], 'complete')
        launch.assert_not_called()

    def test_original_error_or_stop_never_restarts_it(self):
        self.publish(self.output / 'pipeline-state.json', {'state': 'error'})
        with patch.object(queued, 'launch_follow_on') as launch:
            status = queued.run(self.path)
        self.assertEqual(status['state'], 'blocked')
        launch.assert_not_called()
        self.complete()
        (self.output / 'pipeline.stop').write_text('stop')
        with self.assertRaisesRegex(queued.QueueBlocked, 'stop was requested'):
            queued.activate(self.pending)


if __name__ == '__main__':
    unittest.main()
