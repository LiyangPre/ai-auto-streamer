"""Metadata-only recovery faults; no real worker, provider or live batch access."""
from contextlib import ExitStack, nullcontext
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import queue_astra_song as queued

coordinator = queued.coordinator


class CoordinatorTelemetryTests(unittest.TestCase):
    def test_explicit_busy_import_waits_beyond_generic_budget_without_new_work(self):
        for code in ('stage_busy', 'preparation_busy'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                detail = io.BytesIO(json.dumps({'detail': {'code': code, 'message': 'Wait'}}).encode())
                busy = coordinator.urllib.error.HTTPError('http://127.0.0.1', 409, 'busy', {}, detail)
                response = io.BytesIO(b'{"playback_started":false}')
                with patch.object(coordinator, 'OUTPUT', Path(directory)), \
                        patch.object(coordinator, 'assert_voice') as voice, \
                        patch.object(coordinator.time, 'monotonic', side_effect=[0, 10000]), \
                        patch.object(coordinator.time, 'sleep') as sleep, \
                        patch.object(coordinator.urllib.request, 'urlopen', side_effect=[busy, response]):
                    self.assertFalse(coordinator.import_library({}, {})['playback_started'])
                self.assertEqual(voice.call_count, 2)
                sleep.assert_called_once_with(5)

    def test_offline_fallback_preserves_explicit_replacement_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'import-result.json').write_text('{"playback_started":false}', encoding='utf-8')
            jobs = {'replace_titles': ['海屿你', '泡沫']}
            refusal = coordinator.urllib.error.URLError(ConnectionRefusedError())
            with patch.object(coordinator, 'OUTPUT', output), \
                    patch.object(coordinator, 'assert_voice'), \
                    patch.object(coordinator.urllib.request, 'urlopen', side_effect=refusal), \
                    patch.object(coordinator.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                result = coordinator.import_library(jobs, {})
            self.assertFalse(result['playback_started'])
            command = run.call_args.args[0]
            self.assertEqual(command[-4:], ['--replace-title', '海屿你', '--replace-title', '泡沫'])
            self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_telemetry_publication_is_best_effort_and_keeps_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pipeline-state.json'
            before = b'{"state":"preparing","ready":2}'
            path.write_bytes(before)
            with patch.object(coordinator, 'write_json_atomic', return_value=False) as writer:
                self.assertFalse(coordinator.publish_telemetry(path, {'ready': 3}))
            self.assertTrue(writer.call_args.kwargs['best_effort'])
            self.assertEqual(path.read_bytes(), before)

    def test_repeated_status_write_failures_do_not_terminate_running_workers(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            output = root / 'data/songs-converted'
            output.mkdir(parents=True)
            state_path = output / 'pipeline-state.json'
            prior = {'state': 'preparing', 'ready': 0, 'total': 1, 'updated': 1}
            state_path.write_text(json.dumps(prior), encoding='utf-8')
            reference_path = root / 'reference.json'
            reference_path.write_text('{}', encoding='utf-8')
            jobs = {'reference_metadata': str(reference_path), 'reference': str(root / 'voice.wav'),
                    'reference_sha256': 'a' * 64, 'voice_candidate': 'current-profile',
                    'tracks': [{'id': 'BV0000000001', 'bvid': 'BV0000000001', 'source_duration': 10}]}
            (output / 'jobs.json').write_text(json.dumps(jobs), encoding='utf-8')
            (root / 'data/song-requests.json').write_text('{"tracks":[]}', encoding='utf-8')
            children = [(Mock(pid=101), Mock()), (Mock(pid=102), Mock())]
            for process, _ in children:
                process.poll.return_value = None
            ready = {'bvid': 'BV0000000001', 'mix_mp3': 'mix.mp3', 'vocals_mp3': 'vocals.mp3', 'duration_seconds': 10}
            failures = 0

            def persist(path, value, **options):
                nonlocal failures
                if Path(path) == state_path and failures < 2:
                    failures += 1
                    self.assertTrue(options.get('best_effort'))
                    return False
                Path(path).write_text(json.dumps(value), encoding='utf-8')
                return True

            def check_still_running(seconds):
                self.assertEqual(seconds, 5)
                stop_worker.assert_not_called()
                self.assertEqual(json.loads(state_path.read_text(encoding='utf-8')), prior)

            for name, value in [('ROOT', root), ('OUTPUT', output), ('STATE', state_path)]:
                stack.enter_context(patch.object(coordinator, name, value))
            stack.enter_context(patch.object(coordinator.sys, 'argv', ['prepare_astra_songs.py', '--steps', '50', '--workers', '1']))
            stack.enter_context(patch.object(coordinator, 'exclusive_job', side_effect=lambda: nullcontext()))
            stack.enter_context(patch.object(coordinator, 'assert_voice'))
            stack.enter_context(patch.object(coordinator, 'worker_limit', return_value=1))
            stack.enter_context(patch.object(coordinator, 'launch', side_effect=children))
            stop_worker = stack.enter_context(patch.object(coordinator, 'stop_worker'))
            stack.enter_context(patch.object(coordinator, 'export_track', side_effect=[None, ready]))
            stack.enter_context(patch.object(coordinator, 'write_json_atomic', side_effect=persist))
            stack.enter_context(patch.object(coordinator, 'publish', side_effect=persist))
            importer = stack.enter_context(patch.object(coordinator, 'import_library', return_value={'imported': [], 'playback_started': False}))
            stack.enter_context(patch.object(coordinator.time, 'sleep', side_effect=check_still_running))
            coordinator.main()
            self.assertEqual(failures, 2)
            importer.assert_called_once()
            self.assertEqual(json.loads(state_path.read_text(encoding='utf-8'))['state'], 'complete')
            self.assertEqual(stop_worker.call_count, 2, 'owned workers are cleaned up only after normal completion')


class CoordinatorLeaseTests(unittest.TestCase):
    def test_recent_heartbeat_does_not_probe_or_claim_a_dead_coordinator(self):
        with patch.object(coordinator.time, 'time', return_value=1000), \
                patch.object(coordinator, 'exclusive_job') as lease:
            self.assertFalse(coordinator.coordinator_is_stale({'state': 'preparing', 'updated': 995}))
        lease.assert_not_called()

    def test_old_heartbeat_is_stale_only_if_the_coordinator_lease_is_free(self):
        stale = {'state': 'preparing', 'updated': 900}
        with patch.object(coordinator.time, 'time', return_value=1000):
            with patch.object(coordinator, 'exclusive_job', side_effect=BlockingIOError('owned lease')):
                self.assertFalse(coordinator.coordinator_is_stale(stale))
            with patch.object(coordinator, 'exclusive_job', side_effect=lambda: nullcontext()):
                self.assertTrue(coordinator.coordinator_is_stale(stale))


class QueueStaleCoordinatorTests(unittest.TestCase):
    def test_stale_original_batch_is_reported_without_restarting_or_editing_it(self):
        # Reuse only the temporary metadata fixture; do not inherit its test cases.
        module = importlib.import_module('tests.test_song_follow_on_queue')
        fixture = module.FollowOnQueueTests('test_waiting_does_not_edit_running_manifests_or_launch_workers')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        paths = [fixture.output / 'jobs.json', fixture.output / 'sources.json', fixture.root / 'data/song-requests.json']
        before = {path: path.read_bytes() for path in paths}
        with patch.object(coordinator, 'coordinator_is_stale', return_value=True) as probe, \
                patch.object(queued, 'launch_follow_on') as launch, \
                patch.object(coordinator, 'stop_worker') as stop:
            status = queued.run(fixture.path)
        probe.assert_called()
        self.assertEqual(status['state'], 'blocked')
        launch.assert_not_called();stop.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
