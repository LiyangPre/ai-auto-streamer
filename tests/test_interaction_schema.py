"""Malformed structured replies fail inside the provider fallback boundary."""
import json
import unittest

from src.interaction_schema import parse_interaction
from src.llm import CallParams, LLMError
from tests.test_glm_fallback import PRIMARY, SECOND, THIRD, processor, reply


def envelope(text='你好呀。', *, emotion='happy', intents=()):
    return json.dumps({'reply': text, 'emotion': emotion, 'intents': list(intents)}, ensure_ascii=False)


def intent(**overrides):
    return {'index': 0, 'intent': 'singing', 'confidence': .95, 'title': None,
            'positive': True, 'current_request': True} | overrides


class InteractionSchemaTests(unittest.TestCase):
    def test_service_reexports_shared_schema_without_duplicate_definitions(self):
        from src import audience_interaction, interaction_schema
        for name in ('IndexedIntent', 'InteractionReply', 'parse_interaction'):
            self.assertIs(getattr(audience_interaction, name), getattr(interaction_schema, name))

    def test_only_known_leading_emotion_tag_is_removed_from_spoken_reply(self):
        for tag in ('happy', 'sad', 'angry', 'surprised', 'thinking', 'normal', 'HAPPY'):
            with self.subTest(tag=tag):
                parsed = parse_interaction(envelope('  ['+tag+'] 你好呀。', emotion='normal'), 1)
                self.assertEqual(parsed.reply, '你好呀。')
                self.assertEqual(parsed.emotion, 'normal')  # Explicit metadata remains authoritative.

    def test_unknown_brackets_interior_tags_and_title_are_preserved(self):
        for text in ('[song] 这是歌名。', '标签 [happy] 在句子里面。', '普通的问候。'):
            with self.subTest(text=text):
                data = parse_interaction(envelope(text, intents=[intent(title='[happy]')]), 1)
                self.assertEqual(data.reply, text)
                self.assertEqual(data.intents[0].title, '[happy]')
                self.assertEqual(data.intents[0].confidence, .95)

    def test_blank_or_emotion_only_speech_is_rejected(self):
        for text in ('', '  ', '[happy]', ' \n[normal]  '):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_interaction(envelope(text), 1)

    def test_exact_json_fence_is_supported_without_extracting_arbitrary_prose(self):
        text = envelope(intents=[intent()])
        self.assertEqual(parse_interaction(' \n```json\n'+text+'\n```\n ', 1).reply, '你好呀。')
        for wrapped in ('Here is the JSON: '+text,
                        'explanation\n```json\n'+text+'\n```',
                        '```json\n'+text+'\n```\nafterword',
                        '```javascript\n'+text+'\n```',
                        '[happy] '+text,
                        text+'\n'+text):
            with self.subTest(wrapped=wrapped), self.assertRaises(ValueError):
                parse_interaction(wrapped, 1)

    def test_enum_alternatives_and_unknown_fields_cannot_pass_validation(self):
        for raw in (envelope(intents=[intent(intent='singing|none')]),
                    envelope(emotion='happy|normal'),
                    envelope(intents=[intent(confidence='0.95')]),
                    envelope(intents=[intent(positive='true')]),
                    envelope(intents=[intent(command='play')]),
                    json.dumps({'reply': '你好', 'action': 'play_song'})):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_interaction(raw, 16, 240)

    def test_caller_rechecks_exact_index_and_shorter_spoken_limit(self):
        raw = envelope('完整的一句话。', intents=[intent(index=15)])
        self.assertEqual(parse_interaction(raw, 16, 240).intents[0].index, 15)
        with self.assertRaises(ValueError):
            parse_interaction(raw, 1, 120)
        with self.assertRaises(ValueError):
            parse_interaction(raw, 16, 3)
        with self.assertRaises(ValueError):
            parse_interaction(envelope(intents=[intent(), intent()]), 16, 240)

    def test_complete_batch_requires_every_message_even_when_it_has_no_intent(self):
        none = intent(index=1, intent='none', positive=False, current_request=False)
        raw = envelope(intents=[none, intent()])
        self.assertEqual(len(parse_interaction(raw, 2, require_all=True).intents), 2)
        for incomplete in ([], [intent()], [none]):
            with self.subTest(incomplete=incomplete), self.assertRaises(ValueError):
                parse_interaction(envelope(intents=incomplete), 2, require_all=True)
        self.assertEqual(parse_interaction(envelope(), 0, require_all=True).intents, [])


class StructuredFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_batch_falls_back_before_it_can_drop_a_viewer(self):
        first = envelope(intents=[intent()])
        complete = envelope(intents=[intent(), intent(index=1, intent='story')])
        async with processor({PRIMARY: [reply(first)], SECOND: [reply(complete)]}) as (engine, client):
            text, _ = await engine.chat('two independent messages', CallParams(
                structured_json=True, structured_message_count=2, max_tokens=4000))
            parsed = parse_interaction(text, 2, require_all=True)
            self.assertEqual([d.intent for d in parsed.intents], ['singing', 'story'])
            self.assertEqual([c['model'] for c in client.calls], [PRIMARY, SECOND])
            self.assertTrue(all(c['max_tokens'] == 2048 for c in client.calls))
            self.assertEqual(engine.fallback_status()['attempts'][0]['code'], 'response')

    async def test_malformed_structured_result_falls_back_with_thinking_disabled(self):
        malformed = envelope(intents=[intent(intent='singing|none')])
        valid = envelope('[happy] 想听什么歌呀？', intents=[intent()])
        backup = 'glm-4.5-air'
        async with processor({PRIMARY: [reply(malformed)], backup: [reply(valid)]},
                             fallback_models=[backup]) as (engine, client):
            text, emotion = await engine.chat('想听你唱歌', CallParams(structured_json=True))
            parsed = parse_interaction(text, 1)
            self.assertEqual(parsed.reply, '想听什么歌呀？')
            self.assertEqual(parsed.intents[0].intent, 'singing')
            self.assertEqual(emotion, 'happy')
            self.assertEqual([call['model'] for call in client.calls], [PRIMARY, backup])
            for call in client.calls:
                self.assertEqual(call['extra_body'], {'thinking': {'type': 'disabled'}})
            status = engine.fallback_status()
            self.assertEqual(status['attempts'][0]['code'], 'response')
            self.assertFalse(status['attempts'][0]['ok'])
            self.assertEqual(status['last_model'], backup)
            self.assertIn(PRIMARY, status['cooldowns'])

    async def test_canonical_json_preserves_metadata_and_never_returns_fences(self):
        data = envelope('[happy] 记得这首歌。', emotion='thinking',
                        intents=[intent(title='[normal]')])
        async with processor({PRIMARY: [reply('```json\n'+data+'\n```')]}) as (engine, client):
            text, emotion = await engine.chat('test', CallParams(structured_json=True))
            value = json.loads(text)
            self.assertEqual(value['reply'], '记得这首歌。')
            self.assertEqual(value['emotion'], 'thinking')
            self.assertEqual(value['intents'], [intent(title='[normal]')])
            self.assertEqual(emotion, 'thinking')
            self.assertEqual(len(client.calls), 1)

    async def test_bad_response_diagnostic_and_logs_never_echo_model_payload(self):
        secret = 'private-payload-do-not-log'
        async with processor({PRIMARY: [reply(secret)]}, fallback_models=[]) as (engine, client):
            with self.assertLogs('src.llm', level='ERROR') as logs:
                with self.assertRaises(LLMError) as caught:
                    await engine.chat('test', CallParams(structured_json=True))
            self.assertEqual(caught.exception.diagnostic['code'], 'response')
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, '\n'.join(logs.output))
            self.assertEqual(len(client.calls), 1)

    async def test_reasoning_or_content_review_checks_precede_json_validation(self):
        for reason, reasoning, diagnostic in (('stop', 'hidden chain', 'reasoning_enabled'),
                                              ('sensitive', None, 'blocked')):
            with self.subTest(diagnostic=diagnostic):
                async with processor({PRIMARY: [reply(envelope(), reason=reason, reasoning=reasoning)]},
                                     fallback_models=[]) as (engine, client):
                    with self.assertRaises(LLMError) as caught:
                        await engine.chat('test', CallParams(structured_json=True))
                    self.assertEqual(caught.exception.diagnostic['code'], diagnostic)
                    self.assertEqual(len(client.calls), 1)

    async def test_exhausted_invalid_models_fail_instead_of_returning_partial_json(self):
        scripts = {model: [reply('{"reply":')] for model in (PRIMARY, SECOND, THIRD)}
        async with processor(scripts) as (engine, client):
            with self.assertRaises(LLMError) as caught:
                await engine.chat('test', CallParams(structured_json=True))
            self.assertEqual(caught.exception.diagnostic['code'], 'response')
            self.assertEqual([call['model'] for call in client.calls], [PRIMARY, SECOND, THIRD])
            self.assertIsNone(engine.last_model)

    async def test_unstructured_chat_keeps_its_existing_text_contract(self):
        async with processor({PRIMARY: [reply('[happy] 普通聊天呀。')]}) as (engine, client):
            self.assertEqual(await engine.chat('test'), ('普通聊天呀。', 'happy'))
            self.assertEqual(len(client.calls), 1)


if __name__ == '__main__':
    unittest.main()
