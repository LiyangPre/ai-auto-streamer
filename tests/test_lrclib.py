"""Read-only LRCLIB lookups are bounded and polite; no real HTTP requests."""
from collections import deque
from datetime import datetime, timezone
from email.utils import format_datetime
import io
import json
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit

from src.lrclib import LRCLib, LyricsLookupError, USER_AGENT


SECRET = 'private-fixture-value-never-returned'


class FakeClock:
    def __init__(self):
        self.now = 100.
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise AssertionError('Negative sleep')
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, value=None, *, raw=None, content_type='application/json; charset=utf-8'):
        self.raw = raw if raw is not None else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.headers = {'Content-Type': content_type}
        self.read_limits = []
        self.closed = False
        self.owner = None

    def read(self, limit):
        self.read_limits.append(limit)
        return self.raw[:limit]

    def __enter__(self):
        self.owner.active = True
        return self

    def __exit__(self, *_):
        self.closed = True
        self.owner.active = False


class Opener:
    def __init__(self, clock, responses):
        self.clock = clock
        self.responses = deque(responses)
        self.requests = []
        self.active = False

    def open(self, request, *, timeout):
        if self.active:
            raise AssertionError('Previous response still open')
        self.requests.append((request, timeout, self.clock()))
        if not self.responses:
            raise AssertionError('Unexpected extra HTTP request')
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        response.owner = self
        return response


def http_error(status, retry=None):
    headers = {} if retry is None else {'Retry-After': retry}
    return urllib.error.HTTPError(
        'https://lrclib.net/api/search?private=' + SECRET,
        status, SECRET, headers, io.BytesIO(SECRET.encode()),
    )


class LRCLibTests(unittest.TestCase):
    def client(self, *responses):
        clock = FakeClock()
        opener = Opener(clock, responses)
        return LRCLib(opener=opener, sleep=clock.sleep, clock=clock), opener, clock

    def test_search_and_get_only_send_encoded_song_metadata_in_read_only_requests(self):
        search_response = Response([{'id': 7, 'trackName': '虚构歌名'}])
        get_response = Response({'id': 7, 'trackName': '虚构歌名'})
        client, opener, _ = self.client(search_response, get_response)
        self.assertEqual(client.search('虚构歌名 & 清晨', '示例歌手+甲')[0]['id'], 7)
        self.assertEqual(client.get(7)['id'], 7)
        search, timeout, _ = opener.requests[0]
        self.assertEqual(parse_qs(urlsplit(search.full_url).query), {
            'track_name': ['虚构歌名 & 清晨'], 'artist_name': ['示例歌手+甲'],
        })
        self.assertEqual(timeout, 20)
        self.assertTrue(search_response.closed)
        self.assertTrue(get_response.closed)
        for request, _, _ in opener.requests:
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(urlsplit(request.full_url).hostname, 'lrclib.net')
            self.assertNotIn('publish', urlsplit(request.full_url).path)
        self.assertEqual(urlsplit(opener.requests[1][0].full_url).path, '/api/get/7')

    def test_request_identifier_is_fixed_public_and_does_not_include_environment_secrets(self):
        client, opener, _ = self.client(Response([]))
        with patch.dict('os.environ', {
            'GLM_API_KEY': SECRET, 'MIMO_API_KEY': SECRET,
            'HTTPS_PROXY': 'http://user:' + SECRET + '@proxy.invalid:8080',
        }):
            client.search('虚构歌名')
        request = opener.requests[0][0]
        headers = dict(request.header_items())
        self.assertEqual(request.get_header('User-agent'), USER_AGENT)
        self.assertIn('https://lrclib.net/docs', USER_AGENT)
        self.assertNotIn('github', USER_AGENT.lower())
        self.assertNotIn(SECRET, str(headers))
        self.assertNotIn('authorization', {name.lower() for name in headers})
        self.assertNotIn('cookie', {name.lower() for name in headers})
        self.assertNotIn('artist_name', parse_qs(urlsplit(request.full_url).query))

    def test_separate_successful_requests_are_throttled_and_response_is_closed_first(self):
        client, opener, clock = self.client(Response([]), Response({'id': 8}), Response([]))
        client.search('虚构甲')
        client.get(8)
        client.search('虚构乙')
        times = [at for _, _, at in opener.requests]
        self.assertGreaterEqual(times[1] - times[0], .35 - 1e-9)
        self.assertGreaterEqual(times[2] - times[1], .35 - 1e-9)
        self.assertTrue(all(seconds <= .35 + 1e-9 for seconds in clock.sleeps))

    def test_transient_http_errors_honor_retry_after_before_sequential_retry(self):
        client, opener, _ = self.client(http_error(429, '2.5'), http_error(503, '3'), Response([]))
        self.assertEqual(client.search('虚构歌名'), [])
        times = [at for _, _, at in opener.requests]
        self.assertGreaterEqual(times[1] - times[0], 2.5)
        self.assertGreaterEqual(times[2] - times[1], 3)

    def test_retry_after_http_date_is_respected(self):
        wall_now = 1793145600.
        retry_date = format_datetime(datetime.fromtimestamp(wall_now + 8, timezone.utc), usegmt=True)
        client, opener, _ = self.client(http_error(503, retry_date), Response({'id': 9}))
        with patch('src.lrclib.time.time', return_value=wall_now):
            self.assertEqual(client.get(9)['id'], 9)
        self.assertGreaterEqual(opener.requests[1][2] - opener.requests[0][2], 8)

    def test_long_or_nonfinite_retry_after_stops_without_hammering_or_long_blocking_sleep(self):
        for header in ('61', '3600', 'inf', 'nan'):
            with self.subTest(header=header):
                client, opener, clock = self.client(http_error(429, header), Response([]))
                with self.assertRaises(LyricsLookupError) as caught:
                    client.search('虚构歌名')
                self.assertEqual(len(opener.requests), 1)
                self.assertTrue(all(seconds <= .35 for seconds in clock.sleeps))
                self.assertIn('longer pause', str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))

    def test_missing_or_invalid_retry_after_uses_a_short_pause_and_has_a_retry_cap(self):
        for header in (None, 'not-a-date'):
            with self.subTest(header=header):
                client, opener, clock = self.client(*(http_error(503, header) for _ in range(4)))
                with self.assertRaises(LyricsLookupError) as caught:
                    client.search('虚构歌名')
                self.assertEqual(len(opener.requests), 4)
                self.assertEqual(sum(seconds for seconds in clock.sleeps if seconds >= 1), 3)
                self.assertIn('HTTP 503', str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))

    def test_not_found_has_empty_search_or_missing_record_semantics_without_retry(self):
        client, opener, _ = self.client(http_error(404), http_error(404))
        self.assertEqual(client.search('不存在的示例'), [])
        self.assertIsNone(client.get(123))
        self.assertEqual(len(opener.requests), 2)

    def test_authentication_and_parameter_errors_are_not_retried_or_exposed(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                client, opener, _ = self.client(http_error(status), Response([]))
                with self.assertRaises(LyricsLookupError) as caught:
                    client.search('虚构歌名')
                self.assertEqual(len(opener.requests), 1)
                self.assertIn(f'HTTP {status}', str(caught.exception))
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn('https://', str(caught.exception))

    def test_response_type_json_and_size_errors_are_bounded_and_sanitized(self):
        cases = [
            Response(raw=SECRET.encode(), content_type='text/html'),
            Response(raw=(SECRET + ' broken json').encode()),
            Response(raw=b'\xff\xfe\x00'),
            Response(raw=b'x' * 128),
        ]
        for response in cases:
            with self.subTest(content_type=response.headers['Content-Type'], size=len(response.raw)):
                client, opener, _ = self.client(response)
                with patch('src.lrclib.MAX_RESPONSE', 64):
                    with self.assertRaises(LyricsLookupError) as caught:
                        client.search('虚构歌名')
                self.assertEqual(len(opener.requests), 1)
                self.assertTrue(response.closed)
                self.assertNotIn(SECRET, str(caught.exception))
                if response.read_limits:
                    self.assertEqual(response.read_limits, [65])

    def test_network_errors_are_sanitized_and_not_retried_implicitly(self):
        client, opener, _ = self.client(urllib.error.URLError(SECRET))
        with self.assertRaises(LyricsLookupError) as caught:
            client.search('虚构歌名')
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn(SECRET, str(caught.exception))

    def test_invalid_envelopes_are_rejected_and_nonrecord_search_items_are_ignored(self):
        client, _, _ = self.client(Response({'message': SECRET}), Response([]),
                                   Response([{'id': 1}, SECRET, None, 99]), Response([{}] * 101))
        with self.assertRaises(LyricsLookupError):
            client.search('虚构歌名')
        with self.assertRaises(LyricsLookupError):
            client.get(1)
        self.assertEqual(client.search('虚构歌名'), [{'id': 1}])
        with self.assertRaises(LyricsLookupError):
            client.search('虚构歌名')

    def test_invalid_inputs_stop_before_any_network_access(self):
        client, opener, _ = self.client()
        for title, artist in [('', ''), ('x' * 161, ''), (None, ''), ('song', None), ('song', 'x' * 161)]:
            with self.subTest(title=title, artist=artist), self.assertRaises(ValueError):
                client.search(title, artist)
        for record_id in (True, False, 0, -1, '123', 1.5, None):
            with self.subTest(record_id=record_id), self.assertRaises(ValueError):
                client.get(record_id)
        self.assertEqual(opener.requests, [])


if __name__ == '__main__':
    unittest.main()
