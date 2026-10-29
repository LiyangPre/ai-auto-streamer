"""GLM fallback behavior under provider failures; no network or provider charges."""
import asyncio
import json
import ssl
import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from src.config import CharacterConfig, LLMConfig
from src.glm_models import DEFAULT_GLM_FALLBACKS, DEFAULT_GLM_MODEL
from src.llm import CallParams, LLMError, LLMProcessor


PRIMARY = 'glm-4.7-flash'
SECOND = 'glm-4-flash-250414'
THIRD = 'glm-4-flashx-250414'
SECRET = 'private-unit-provider-key'


class Rejected(RuntimeError):
    def __init__(self, status):
        self.status_code = status
        super().__init__(f'upstream echoed {SECRET}')


def reply(text='[happy] 你好呀。', *, reason='stop', reasoning=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text, reasoning_content=reasoning), finish_reason=reason)])


def chunk(text=None, *, reason=None, reasoning=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, reasoning_content=reasoning), finish_reason=reason)])


class Waiting:
    def __init__(self, seconds=1):
        self.seconds = seconds
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self):
        self.started.set()
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return None


class Stream:
    def __init__(self, *events):
        self.events = list(events)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self.events:
            event = self.events.pop(0)
            if isinstance(event, Waiting):
                await event.run()
                continue
            if isinstance(event, BaseException):
                raise event
            return chunk(event) if isinstance(event, str) else event
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class SlowClosingStream(Stream):
    def __init__(self, *events):
        super().__init__(*events)
        self.close_wait = Waiting()

    async def close(self):
        await self.close_wait.run()
        self.closed = True


class BrokenClosingStream(Stream):
    async def close(self):
        raise RuntimeError('cleanup echoed ' + SECRET)


class Client:
    def __init__(self, scripts):
        self.scripts = {model: list(events) for model, events in scripts.items()}
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.closed = False

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        script = self.scripts.get(kwargs['model'], [])
        if not script:
            raise AssertionError('Unexpected provider request: ' + kwargs['model'])
        event = script.pop(0)
        if isinstance(event, Waiting):
            await event.run()
            return reply()
        if isinstance(event, BaseException):
            raise event
        return event

    async def close(self):
        self.closed = True


@asynccontextmanager
async def processor(scripts, **overrides):
    client = Client(scripts)
    values = dict(provider='glm', model=PRIMARY, api_key=SECRET, network_mode='direct',
                  fallback_models=[SECOND, THIRD], fallback_attempt_timeout=.05,
                  fallback_total_timeout=.5, fallback_cooldown=1)
    values.update(overrides)
    config = LLMConfig(**values)
    with patch('src.llm.AsyncOpenAI', return_value=client), patch('src.llm.http_client', return_value=None):
        engine = LLMProcessor(config, CharacterConfig())
    try:
        yield engine, client
    finally:
        await engine.close()


async def collect(engine):
    return [item async for item in engine.chat_stream('今天好吗？', CallParams())]


class GLMFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonstream_switches_in_order_on_500_429_and_missing_model(self):
        for status in (500, 429, 404):
            with self.subTest(status=status):
                async with processor({PRIMARY: [Rejected(status)], SECOND: [reply()]}) as (engine, client):
                    self.assertEqual(await engine.chat('你好'), ('你好呀。', 'happy'))
                    self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
                    self.assertEqual(engine.last_model, SECOND)
                    self.assertEqual(engine.fallback_status()['attempts'][0]['http_status'], status)

    async def test_stream_switches_before_first_text_on_provider_errors(self):
        for status in (500, 429, 404):
            with self.subTest(status=status):
                answer = Stream('[ha', 'ppy] 你好', '呀。')
                async with processor({PRIMARY: [Rejected(status)], SECOND: [answer]}) as (engine, client):
                    self.assertEqual(await collect(engine), [('你好', 'happy'), ('呀。', None)])
                    self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
                    self.assertTrue(answer.closed)

    async def test_empty_nonstream_response_switches_to_next_model(self):
        async with processor({PRIMARY: [reply('  ')], SECOND: [reply()]}) as (engine, client):
            self.assertEqual((await engine.chat('你好'))[0], '你好呀。')
            self.assertEqual(len(client.calls), 2)

    async def test_empty_or_emotion_only_streams_do_not_count_as_replies(self):
        for first in (Stream(), Stream('[happy] ')):
            with self.subTest(first=first):
                async with processor({PRIMARY: [first], SECOND: [Stream('答复。')]}) as (engine, client):
                    self.assertEqual(await collect(engine), [('答复。', 'normal')])
                    self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
                    self.assertTrue(first.closed)

    async def test_nonstream_deadline_cancels_hung_request_before_fallback(self):
        hung = Waiting()
        async with processor({PRIMARY: [hung], SECOND: [reply()]}) as (engine, client):
            await engine.chat('你好')
            self.assertTrue(hung.cancelled)
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])

    async def test_stream_request_creation_timeout_can_fall_back(self):
        hung = Waiting()
        async with processor({PRIMARY: [hung], SECOND: [Stream('答复。')]}) as (engine, client):
            self.assertEqual(await collect(engine), [('答复。', 'normal')])
            self.assertTrue(hung.cancelled)
            self.assertEqual(len(client.calls), 2)

    async def test_empty_heartbeat_then_read_timeout_closes_first_stream(self):
        hung = Waiting()
        first = Stream(chunk(), hung)
        async with processor({PRIMARY: [first], SECOND: [Stream('答复。')]}) as (engine, client):
            self.assertEqual(await collect(engine), [('答复。', 'normal')])
            self.assertTrue(hung.cancelled)
            self.assertTrue(first.closed)
            self.assertEqual(len(client.calls), 2)

    async def test_cooldown_skips_failed_model_then_retries_it_after_expiry(self):
        async with processor({PRIMARY: [Rejected(500), reply('恢复啦。')],
                              SECOND: [reply(), reply()]}) as (engine, client):
            await engine.chat('一次')
            self.assertIn(PRIMARY, engine.fallback_status()['cooldowns'])
            await engine.chat('二次')
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND, SECOND])
            await asyncio.sleep(1.02)
            self.assertEqual(await engine.chat('三次'), ('恢复啦。', 'normal'))
            self.assertEqual(client.calls[-1]['model'], PRIMARY)
            self.assertNotIn(PRIMARY, engine.fallback_status()['cooldowns'])

    async def test_total_budget_bounds_all_attempts_for_both_request_modes(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                waits = {model: Waiting() for model in (PRIMARY, SECOND, THIRD)}
                async with processor({m: [w] for m, w in waits.items()},
                                     fallback_attempt_timeout=.06, fallback_total_timeout=.09) as (engine, client):
                    started = time.monotonic()
                    with self.assertRaises(LLMError):
                        await collect(engine) if streaming else await engine.chat('你好')
                    self.assertLess(time.monotonic()-started, .3)
                    self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
                    self.assertTrue(waits[PRIMARY].cancelled)
                    self.assertTrue(waits[SECOND].cancelled)

    async def test_stream_never_splices_another_model_after_text_was_yielded(self):
        first = Stream('[happy] 第一部分。', Rejected(500))
        async with processor({PRIMARY: [first], SECOND: [Stream('另一份回复。')]}) as (engine, client):
            iterator = engine.chat_stream('你好')
            self.assertEqual(await anext(iterator), ('第一部分。', 'happy'))
            with self.assertRaises(LLMError):
                await anext(iterator)
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY])
            self.assertTrue(first.closed)

    async def test_cancelling_a_wait_does_not_start_a_backup_and_closes_stream(self):
        hung = Waiting()
        first = Stream(hung)
        async with processor({PRIMARY: [first], SECOND: [Stream('答复。')]},
                             fallback_attempt_timeout=1) as (engine, client):
            task = asyncio.create_task(collect(engine))
            await asyncio.wait_for(hung.started.wait(), .3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(first.closed)
            self.assertTrue(hung.cancelled)
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY])

    async def test_consumer_closing_after_one_fragment_closes_underlying_response(self):
        first = Stream('第一部分。', Waiting())
        async with processor({PRIMARY: [first]}) as (engine, client):
            iterator = engine.chat_stream('你好')
            await anext(iterator)
            await iterator.aclose()
            self.assertTrue(first.closed)
            self.assertEqual(len(client.calls), 1)

    async def test_stalled_stream_close_is_cancelled_within_the_remaining_budget(self):
        first = SlowClosingStream('[happy] 答复。')
        async with processor({PRIMARY: [first]}, fallback_total_timeout=.09) as (engine, client):
            started = time.monotonic()
            result = await asyncio.wait_for(collect(engine), .4)
            self.assertEqual(result, [('答复。', 'happy')])
            self.assertLess(time.monotonic()-started, .3)
            self.assertTrue(first.close_wait.started.is_set())
            self.assertTrue(first.close_wait.cancelled)
            self.assertEqual(len(client.calls), 1)

    async def test_stream_close_error_cannot_leak_secrets_or_prevent_provider_fallback(self):
        first = BrokenClosingStream(Rejected(500))
        async with processor({PRIMARY: [first], SECOND: [Stream('备用答复。')]}) as (engine, client):
            with self.assertLogs('src.llm', level='DEBUG') as logs:
                result = await collect(engine)
            self.assertEqual(result, [('备用答复。', 'normal')])
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
            self.assertEqual(engine.fallback_status()['attempts'][0]['code'], 'provider_service')
            self.assertNotIn(SECRET, '\n'.join(logs.output))
            self.assertNotIn(SECRET, json.dumps(engine.fallback_status()))

    async def test_cancelled_stream_close_still_propagates_cancellation_without_fallback(self):
        class CancelledClose(Stream):
            async def close(self):
                raise asyncio.CancelledError()

        first = CancelledClose('[happy] 答复。')
        async with processor({PRIMARY: [first]}) as (engine, client):
            with self.assertRaises(asyncio.CancelledError):
                await collect(engine)
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY])

    async def test_all_reasoning_capable_fallback_requests_keep_thinking_disabled(self):
        models = [DEFAULT_GLM_MODEL, *DEFAULT_GLM_FALLBACKS]
        scripts = {model: [Rejected(500)] for model in models}
        scripts[models[-1]] = [reply()]
        async with processor(scripts, model=models[0], fallback_models=models[1:],
                             enable_thinking=True) as (engine, client):
            await engine.chat('你好')
            self.assertFalse(engine.config.enable_thinking)
            self.assertEqual([c['model'] for c in client.calls], models)
            for call in client.calls:
                if call['model'].startswith(('glm-4.5', 'glm-4.7')):
                    self.assertEqual(call['extra_body'], {'thinking': {'type': 'disabled'}})
                else:
                    self.assertNotIn('extra_body', call)

    async def test_thinking_parameter_rejection_never_retries_same_model_without_it(self):
        async with processor({PRIMARY: [Rejected(400)], 'glm-4.5-air': [reply()]},
                             fallback_models=['glm-4.5-air']) as (engine, client):
            await engine.chat('你好')
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, 'glm-4.5-air'])
            for call in client.calls:
                self.assertEqual(call['extra_body'], {'thinking': {'type': 'disabled'}})

    async def test_provider_authentication_rejection_does_not_spam_other_models(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                async with processor({PRIMARY: [Rejected(status)]}) as (engine, client):
                    with self.assertRaises(LLMError):
                        await engine.chat('你好')
                    self.assertEqual(len(client.calls), 1)

    async def test_http_content_review_business_error_remains_blocked_after_llm_wrapping(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                error = Rejected(400)
                error.body = {'error': {'code': '1301', 'message': SECRET}}
                async with processor({PRIMARY: [error]}) as (engine, client):
                    with self.assertRaises(LLMError) as caught:
                        await collect(engine) if streaming else await engine.chat('你好')
                    self.assertEqual(caught.exception.diagnostic['code'], 'blocked')
                    self.assertEqual(caught.exception.diagnostic['http_status'], 400)
                    self.assertEqual(len(client.calls), 1)
                    self.assertEqual(engine.fallback_status()['cooldowns'], {})
                    self.assertNotIn(SECRET, str(caught.exception))

    async def test_account_balance_429_stops_but_model_congestion_429_falls_back(self):
        for streaming in (False, True):
            for business_code, should_switch in ((1113, False), ('1305', True)):
                with self.subTest(streaming=streaming, business_code=business_code):
                    error = Rejected(429)
                    error.body = {'error': {'code': business_code, 'message': SECRET}}
                    backup = Stream('答复。') if streaming else reply('答复。')
                    async with processor({PRIMARY: [error], SECOND: [backup]}) as (engine, client):
                        if should_switch:
                            result = await collect(engine) if streaming else await engine.chat('你好')
                            self.assertIn('答复。', str(result))
                            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
                            self.assertEqual(engine.fallback_status()['attempts'][0]['code'], 'quota')
                        else:
                            with self.assertRaises(LLMError) as caught:
                                await collect(engine) if streaming else await engine.chat('你好')
                            self.assertEqual(caught.exception.diagnostic['code'], 'account_limit')
                            self.assertEqual(len(client.calls), 1)
                            self.assertNotIn(SECRET, str(caught.exception))

    async def test_certificate_failure_does_not_fall_back_or_disable_verification(self):
        error = ssl.SSLCertVerificationError(1, 'CERTIFICATE_VERIFY_FAILED')
        async with processor({PRIMARY: [error]}) as (engine, client):
            with self.assertRaises(LLMError) as caught:
                await engine.chat('你好')
            self.assertEqual(caught.exception.diagnostic['code'], 'tls')
            self.assertEqual(len(client.calls), 1)

    async def test_failed_models_all_cooling_down_return_without_another_network_request(self):
        async with processor({PRIMARY: [Rejected(500)], SECOND: [Rejected(500)]},
                             fallback_models=[SECOND]) as (engine, client):
            with self.assertRaises(LLMError):
                await engine.chat('第一次')
            with self.assertRaises(LLMError) as caught:
                await engine.chat('第二次')
            self.assertEqual(caught.exception.diagnostic['code'], 'models_cooling_down')
            self.assertEqual(len(client.calls), 2)

    async def test_failures_logs_and_public_attempts_do_not_contain_upstream_secrets(self):
        async with processor({PRIMARY: [Rejected(500)], SECOND: [Rejected(500)]},
                             fallback_models=[SECOND]) as (engine, client):
            with self.assertLogs('src.llm', level='ERROR') as logs:
                with self.assertRaises(LLMError) as caught:
                    await engine.chat('你好')
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertNotIn(SECRET, ''.join(logs.output))
            self.assertNotIn(SECRET, json.dumps(engine.fallback_status()))

    async def test_consumer_delay_does_not_count_as_model_io_deadline(self):
        first = Stream('[happy] 第一句。', '第二句。')
        async with processor({PRIMARY: [first]}, fallback_total_timeout=.08) as (engine, client):
            iterator = engine.chat_stream('你好')
            self.assertEqual(await anext(iterator), ('第一句。', 'happy'))
            await asyncio.sleep(.15)
            self.assertEqual(await anext(iterator), ('第二句。', None))
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
            self.assertEqual(len(client.calls), 1)

    async def test_provider_content_filter_is_not_bypassed_with_another_model(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                blocked = Stream(chunk(reason='sensitive')) if streaming else reply('', reason='sensitive')
                backup = Stream('备用回复。') if streaming else reply('备用回复。')
                async with processor({PRIMARY: [blocked], SECOND: [backup]}) as (engine, client):
                    with self.assertRaises(LLMError) as caught:
                        await collect(engine) if streaming else await engine.chat('你好')
                    self.assertEqual(caught.exception.diagnostic['code'], 'blocked')
                    self.assertEqual(len(client.calls), 1)

    async def test_provider_ignoring_disabled_thinking_is_not_allowed_to_emit_reasoning(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                first = (Stream(chunk(reasoning='private unrequested reasoning')) if streaming
                         else reply('first answer', reasoning='private unrequested reasoning'))
                second = Stream('答复。') if streaming else reply('答复。')
                async with processor({PRIMARY: [first], SECOND: [second]}) as (engine, client):
                    result = await collect(engine) if streaming else await engine.chat('你好')
                    self.assertIn('答复。', str(result))
                    self.assertNotIn('private', str(result))
                    self.assertEqual(engine.fallback_status()['attempts'][0]['code'], 'reasoning_enabled')
                    self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])


class GLMNoThinkingConfigurationTests(unittest.TestCase):
    def test_forced_thinking_models_are_rejected_as_primary_or_fallback(self):
        for model in ('glm-5.3', 'glm-5.3-flash', 'glm-z1-flash', 'glm-4.1v-thinking-flash'):
            with self.subTest(model=model):
                with self.assertRaises(ValidationError):
                    LLMConfig(provider='glm', model=model)
                with self.assertRaises(ValidationError):
                    LLMConfig(provider='glm', model=PRIMARY, fallback_models=[model])

    def test_deprecated_flash_alias_is_not_mistaken_for_an_independent_backup(self):
        with self.assertRaises(ValidationError):
            LLMConfig(provider='glm', model=PRIMARY, fallback_models=['glm-4.5-flash'])


if __name__ == '__main__':
    unittest.main()
