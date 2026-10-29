"""Offline launcher checks: secured local probe and owned process lifecycle."""
import contextlib
import io
import urllib.error
import urllib.request
import unittest
from unittest.mock import Mock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.config import Config
from src.local_security import local_request
from scripts import start_studio


class LauncherLifecycleTests(unittest.TestCase):
    def run_main(self, arguments, ready, process=None, clock=None):
        config = Config()
        config.server.host = '127.0.0.1'
        config.server.port = 8000
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(start_studio.sys, 'argv', ['start_studio.py', *arguments]))
            stack.enter_context(patch.object(start_studio.Config, 'load', return_value=config))
            stack.enter_context(patch.object(start_studio, 'studio_ready', side_effect=ready))
            spawn = stack.enter_context(patch.object(start_studio.subprocess, 'Popen', return_value=process))
            stop = stack.enter_context(patch.object(start_studio, 'stop_server'))
            pages = stack.enter_context(patch.object(start_studio.webbrowser, 'open_new_tab'))
            stack.enter_context(patch.object(start_studio.time, 'sleep'))
            if clock is not None:
                stack.enter_context(patch.object(start_studio.time, 'monotonic', side_effect=clock))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            code = start_studio.main()
        return code, spawn, stop, pages, output.getvalue()

    def test_existing_healthy_studio_is_reused_without_spawning(self):
        code, spawn, stop, pages, output = self.run_main([], ready=[True])
        self.assertEqual(code, 0)
        spawn.assert_not_called()
        stop.assert_not_called()
        self.assertEqual([call.args[0] for call in pages.call_args_list], [
            'http://127.0.0.1:8000/control', 'http://127.0.0.1:8000/?overlay=1&controls=0'])
        self.assertIn('already running', output)

    def test_new_owned_server_waits_for_readiness_and_keeps_running(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        code, spawn, stop, pages, output = self.run_main(['--no-browser'], ready=[False, False, True],
                                                   process=process, clock=[0, 1, 45])
        self.assertEqual(code, 0)
        spawn.assert_called_once()
        self.assertIn(str(start_studio.ROOT / 'main.py'), spawn.call_args.args[0])
        process.wait.assert_called_once_with()
        process.terminate.assert_not_called()
        stop.assert_not_called()
        pages.assert_not_called()
        self.assertIn('Keep this console open', output)

    def test_timeout_stops_only_the_owned_new_server(self):
        process = Mock()
        process.poll.return_value = None
        code, spawn, stop, pages, output = self.run_main(['--no-browser'], ready=[False],
                                                   process=process, clock=[0, 121])
        self.assertEqual(code, 1)
        spawn.assert_called_once()
        stop.assert_called_once_with(process)
        pages.assert_not_called()
        self.assertIn('timed out', output)

    def test_windows_stop_targets_only_owned_server_tree(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(start_studio.sys, 'platform', 'win32'), \
                patch.object(start_studio.subprocess, 'run') as command:
            start_studio.stop_server(process)
        self.assertEqual(command.call_args.args[0], ['taskkill', '/PID', '12345', '/T', '/F'])
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=5)

    def test_stop_leaves_already_exited_server_alone(self):
        process = Mock()
        process.poll.return_value = 0
        with patch.object(start_studio.subprocess, 'run') as command:
            start_studio.stop_server(process)
        command.assert_not_called()
        process.terminate.assert_not_called()
        process.wait.assert_not_called()


class LauncherReadinessTests(unittest.TestCase):
    def test_probe_bypasses_proxies_and_satisfies_real_local_api_boundary(self):
        app = FastAPI()
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1'])

        @app.get('/api/config', dependencies=[Depends(local_request)])
        async def config():
            return {'renderer': 'sprite', 'platform': 'text', 'tts_engine': 'none'}

        # Bridge urllib's mocked transport to a real ASGI route with our actual
        # dependency. No network socket or model/voice provider is involved.
        client = TestClient(app, base_url='http://127.0.0.1')

        def open_request(request, timeout):
            self.assertEqual(timeout, 1)
            self.assertEqual(request.full_url, 'http://127.0.0.1/api/config')
            headers = dict(request.header_items())
            self.assertEqual({name.lower(): value for name, value in headers.items()}['x-studio-api'], '1')
            response = client.get(request.full_url, headers=headers)
            if response.status_code >= 400:
                raise urllib.error.HTTPError(request.full_url, response.status_code, 'denied', {}, None)
            return io.BytesIO(response.content)

        opener = Mock()
        opener.open.side_effect = open_request
        with patch.object(start_studio.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertTrue(start_studio.studio_ready('http://127.0.0.1'))
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})

    def test_http_denial_and_non_studio_responses_are_not_ready(self):
        cases = [urllib.error.HTTPError('http://127.0.0.1/api/config', 403, 'denied', {}, None),
                 b'not json', b'[]', b'{}']
        for value in cases:
            with self.subTest(value=value):
                opener = Mock()
                if isinstance(value, Exception):
                    opener.open.side_effect = value
                else:
                    opener.open.return_value = io.BytesIO(value)
                with patch.object(start_studio.urllib.request, 'build_opener', return_value=opener):
                    self.assertFalse(start_studio.studio_ready('http://127.0.0.1'))
