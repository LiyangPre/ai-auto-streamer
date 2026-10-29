import asyncio
import json
import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from src.api_settings import ConnectionStore, ConnectionUpdate, connection_path
from src.config import Config, LLMConfig, CharacterConfig
from src.llm import LLMProcessor, LLMError, CallParams
from src.provider_network import route, failure, check_response, ProviderResponseError
from src.server import app
import src.server as server


class ConnectionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'settings.json'
        self.config=Config();self.config.llm.provider='mock';self.config.tts.engine='none';self.config.idle.enabled=False
        self.patches=[patch('src.server.Config.load',return_value=self.config),
            patch('src.server.settings_path',return_value=self.path),patch('src.danmaku.sys.stdin.isatty',return_value=False),
            patch.dict(os.environ,{'GLM_API_KEY':'env-test-glm','MIMO_API_KEY':''})]
        for p in self.patches:p.start()
        self.client=TestClient(app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'});self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def payload(self):
        value=self.client.get('/api/connections').json()
        return {k:value[k] for k in ('revision','text','mimo')}

    def test_keys_save_privately_apply_immediately_and_never_echo(self):
        data=self.payload();data['keys']={'text':'private-unit-text','mimo-tts':'private-unit-voice'}
        data['text'].update(provider='glm',model='glm-4.7-flashx',base_url='https://open.bigmodel.cn/api/paas/v4',network_mode='direct')
        response=self.client.post('/api/connections',json=data)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.config.llm.resolve_api_key(),'private-unit-text')

        self.assertEqual(self.config.tts.resolve_api_key('mimo-tts'),'private-unit-voice')
        self.assertEqual(self.config.tts.resolve_api_key('glm-tts'),'private-unit-text')
        self.assertFalse(self.config.llm.enable_thinking)
        self.assertTrue(response.json()['configured']['text'])
        self.assertTrue(response.json()['configured']['mimo-tts'])
        private=connection_path(self.path)
        self.assertTrue(private.exists())
        fresh=Config();store=ConnectionStore(private,fresh);store.load();store.apply(fresh)
        self.assertEqual(fresh.llm.model,'glm-4.7-flashx')
        self.assertEqual(fresh.tts.resolve_api_key('mimo-tts'),'private-unit-voice')
        for endpoint in ['/api/connections','/api/config','/api/studio','/api/status','/api/characters']:
            body=self.client.get(endpoint).text
            for key in ('private-unit-text','private-unit-voice','env-test-glm'):
                self.assertNotIn(key,body)
        self.assertNotIn('private-unit',str(self.config.tts.model_dump()))
        self.assertNotIn('private-unit',server.app.state.character_path.read_text(encoding='utf-8') if server.app.state.character_path.exists() else '')
        blank=self.payload();blank['keys']={'text':'','mimo-tts':''}
        self.assertEqual(self.client.post('/api/connections',json=blank).status_code,200)
        self.assertEqual(self.config.llm.resolve_api_key(),'private-unit-text')

    def test_character_switch_preserves_shared_keys_and_provider_keys_stay_separate(self):
        data=self.payload();data['keys']={'text':'glm-private-test','mimo-tts':'voice-private-test'}
        data['text'].update(provider='glm',model='glm-4.7-flashx',base_url='https://open.bigmodel.cn/api/paas/v4')
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,200)
        context=self.client.get('/api/characters').json()
        created=self.client.post('/api/characters',json={'name':'Mira','source_id':'astra','revision':context['revision']})
        self.assertEqual(created.status_code,200)
        context=self.client.get('/api/characters').json()
        activated=self.client.post('/api/characters/activate',json={'profile_id':created.json()['id'],'revision':context['revision']})
        self.assertEqual(activated.status_code,200)
        self.assertEqual(self.config.tts.resolve_api_key('mimo-tts'),'voice-private-test')
        self.assertEqual(self.config.llm.resolve_api_key(),'glm-private-test')
        data=self.payload();data['text'].update(provider='openai',base_url='https://api.openai.com/v1',model='example');data['keys']={'text':'openai-private-test'}
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,200)
        self.assertEqual(self.config.llm.resolve_api_key(),'openai-private-test')
        self.assertEqual(self.config.tts.resolve_api_key('glm-tts'),'glm-private-test')
        data=self.payload();data['text'].update(provider='glm',base_url='https://open.bigmodel.cn/api/paas/v4',model='glm-4.7-flashx')
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,200)
        self.assertEqual(self.config.llm.resolve_api_key(),'glm-private-test')

    def test_cross_origin_requests_validation_and_conflicts_do_not_leak_or_modify_keys(self):
        data=self.payload();data['keys']={'text':'bad-secret\nnew-line'}
        response=self.client.post('/api/connections',json=data)
        self.assertEqual(response.status_code,422);self.assertNotIn('bad-secret',response.text)
        self.assertFalse(connection_path(self.path).exists())
        data=self.payload()
        for headers in [{'Origin':'https://attacker.example'},{'X-Studio-API':''},
                        {'Host':'attacker.example','Origin':'http://attacker.example'}]:
            self.assertEqual(self.client.post('/api/connections',json=data,headers=headers).status_code,400 if 'Host' in headers else 403)
        data['mimo']['base_url']='https://attacker.example/v1'
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,422)
        data=self.payload();data['revision']=123
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,409)
        data=self.payload();data['text']['enable_thinking']=True
        self.assertEqual(self.client.post('/api/connections',json=data).status_code,422)

    def test_failed_disk_save_preserves_running_settings(self):
        data=self.payload();data['keys']={'text':'new-test-key'}
        original=server.pipeline.llm
        with patch('src.api_settings.ConnectionStore.save',side_effect=OSError('disk full')):
            self.assertEqual(self.client.post('/api/connections',json=data).status_code,500)
        self.assertIs(server.pipeline.llm,original)
        self.assertEqual(server.app.state.connections.revision,0)

    def test_fallback_policy_persists_and_public_catalog_does_not_guess_sizes(self):
        data=self.payload()
        data['text'].update(provider='glm',model='GLM-4.7-Flash',base_url='https://open.bigmodel.cn/api/paas/v4',
                            fallback_models=['glm-4-flash-250414','glm-4-flashx-250414'],
                            fallback_attempt_timeout=3,fallback_total_timeout=20,delay_notice_enabled=True)
        response=self.client.post('/api/connections',json=data)
        self.assertEqual(response.status_code,200,response.text)
        value=response.json()
        self.assertEqual(value['text']['model'],'glm-4.7-flash')
        self.assertEqual(self.config.llm.fallback_models,data['text']['fallback_models'])
        fresh=Config();store=ConnectionStore(connection_path(self.path),fresh);store.load();store.apply(fresh)
        self.assertEqual(fresh.llm.fallback_models,data['text']['fallback_models'])
        catalog={row['id']:row for row in value['glm_catalog']}
        self.assertEqual(catalog['glm-4.7-flash']['total_b'],30)
        self.assertIsNone(catalog['glm-4.7-flashx']['total_b'])
        self.assertNotIn('env-test-glm',response.text)
        self.assertIn('text_runtime',value)

    def test_stream_probe_reports_actual_backup_and_attempts(self):
        data=self.payload();data['target']='text'
        data['text'].update(provider='glm',model='glm-4.7-flash',base_url='https://open.bigmodel.cn/api/paas/v4',
                            fallback_models=['glm-4-flash-250414'])
        async def scripted(engine,*args,model=None,**kwargs):
            if model=='glm-4.7-flash':
                raise LLMError('Temporary service error',{'code':'provider_service','http_status':500,'message':'Temporary service error'})
            yield '连接成功。','normal'
        with patch('src.api_routes.LLMProcessor._chat_stream_one',scripted):
            response=self.client.post('/api/connections/test',json=data)
        value=response.json()
        self.assertTrue(value['ok'],value)
        self.assertEqual(value['model'],'glm-4-flash-250414')
        self.assertEqual([item['ok'] for item in value['attempts']],[False,True])
        self.assertFalse(connection_path(self.path).exists())

    def test_api_rejects_forced_thinking_primary_and_backup(self):
        for primary,backups in [('glm-5.3',[]),('glm-4.7-flash',['glm-5.3-flash'])]:
            data=self.payload();data['text'].update(provider='glm',model=primary,fallback_models=backups)
            self.assertEqual(self.client.post('/api/connections',json=data).status_code,422)

    def test_text_probe_uses_live_stream_mode_and_reports_first_text_without_echo(self):
        data=self.payload();data['target']='text'
        async def chunks(engine,*args):
            yield '',None
            yield 'private probe reply','normal'
        with patch('src.api_routes.MockLLM.chat_stream',chunks),patch('src.api_routes.MockLLM.chat',new=AsyncMock(side_effect=AssertionError('Nonstream called'))):
            response=self.client.post('/api/connections/test',json=data)
        result=response.json()
        self.assertTrue(result['ok'])
        self.assertTrue(result['stream'])
        self.assertGreaterEqual(result['first_text_ms'],0)
        self.assertLessEqual(result['first_text_ms'],result['elapsed_ms'])
        self.assertNotIn('private probe reply',response.text)
        self.assertFalse(connection_path(self.path).exists())

    def test_probe_does_not_call_an_incomplete_text_stream_a_success(self):
        data=self.payload();data['target']='text'
        async def broken(engine,*args):
            yield 'partial reply','normal'
            raise TimeoutError('private timeout detail')
        with patch('src.api_routes.MockLLM.chat_stream',broken):
            response=self.client.post('/api/connections/test',json=data)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(response.json()['code'],'timeout')
        self.assertIn('first_text_ms',response.json())
        self.assertNotIn('private',response.text)

    def test_text_probe_preserves_explicit_nonstream_configuration(self):
        self.config.llm.stream=False
        data=self.payload();data['target']='text'
        with patch('src.api_routes.MockLLM.chat',new=AsyncMock(return_value=('reply','normal'))) as chat:
            response=self.client.post('/api/connections/test',json=data)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(response.json()['stream'])
        chat.assert_awaited_once()

    def test_draft_tests_do_not_save_or_broadcast_and_errors_are_sanitized(self):
        data=self.payload();data.update(target='text',keys={'text':'draft-test-key'})
        result=self.client.post('/api/connections/test',json=data)
        self.assertTrue(result.json()['ok']);self.assertFalse(connection_path(self.path).exists())
        self.assertEqual(self.config.llm.provider,'mock')
        data['target']='mimo-tts';data['keys']={'mimo-tts':'voice-draft-secret'}
        async def chunks(engine,*args):
            self.assertEqual(engine.config.resolve_api_key(),'voice-draft-secret')
            yield b'test-audio'
        with patch('src.api_routes.TTSEngine._audio_chunks',chunks):
            result=self.client.post('/api/connections/test',json=data)
        self.assertTrue(result.json()['ok']);self.assertEqual(result.json()['audio_bytes'],10)
        self.assertFalse(connection_path(self.path).exists())
        async def broken(*args):
            raise httpx.ConnectError('private upstream voice-draft-secret')
            yield b''
        with patch('src.api_routes.TTSEngine._audio_chunks',broken):
            result=self.client.post('/api/connections/test',json=data)
        self.assertEqual(result.json()['code'],'connection')
        self.assertNotIn('voice-draft-secret',result.text)


class NetworkTests(unittest.TestCase):
    def test_tls_io_wait_during_cancellation_is_a_timeout_not_a_bad_certificate(self):
        for waiting in (ssl.SSLWantReadError,ssl.SSLWantWriteError):
            error=TimeoutError('private timeout detail')
            cancelled=asyncio.CancelledError()
            cancelled.__context__=waiting(2,'private TLS I/O detail')
            error.__context__=cancelled
            diagnostic=failure(error)
            self.assertEqual(diagnostic['code'],'timeout')
            self.assertNotIn('certificate',diagnostic['message'])
            self.assertNotIn('private',str(diagnostic))

    def test_certificate_rejection_and_fatal_handshake_remain_tls_errors(self):
        for cause in (ssl.SSLCertVerificationError(1,'CERTIFICATE_VERIFY_FAILED private'),
                      ssl.SSLError(1,'private fatal handshake')):
            error=httpx.ConnectError('private URL')
            error.__cause__=cause
            diagnostic=failure(error)
            self.assertEqual(diagnostic['code'],'tls')
            self.assertNotIn('private',str(diagnostic))

    def test_sdk_wrapped_timeout_takes_precedence_over_transient_ssl_wait(self):
        error=httpx.ConnectError('private URL')
        timeout=httpx.ConnectTimeout('private timeout')
        timeout.__context__=ssl.SSLWantReadError(2,'private TLS wait')
        error.__cause__=timeout
        self.assertEqual(failure(error)['code'],'timeout')

    def test_auto_routes_explicitly_and_never_prints_proxy_credentials(self):
        with patch.dict(os.environ,{},clear=True),patch('src.provider_network.windows_proxy',return_value='http://secret-user:secret-pass@127.0.0.1:7890'):
            self.assertEqual(route('auto')[0],'windows_proxy')
            self.assertEqual(route('direct'),('direct',{'trust_env':False}))
            self.assertEqual(route('environment'),('environment',{'trust_env':True}))
            with patch.dict(os.environ,{'HTTPS_PROXY':'http://proxy.example:8080'}):
                self.assertEqual(route('auto')[0],'environment')
        error=httpx.ConnectError('private-key-containing-url');error.__cause__=socket.gaierror('private detail')
        diagnostic=failure(error)
        self.assertEqual(diagnostic['code'],'dns');self.assertNotIn('private',str(diagnostic))


class FlashXTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_rejections_are_distinguished_without_echoing_body(self):
        for status,body,code in [(401,b'secret-key','authentication'),(500,b'private-data','provider_service'),
                                 (403,b'unsupported_region secret-key','region')]:
            with self.assertRaises(ProviderResponseError) as error:
                await check_response(httpx.Response(status,content=body))
            result=failure(error.exception)
            self.assertEqual(result['code'],code)
            self.assertEqual(result['http_status'],status)
            self.assertNotIn('secret-key',str(result))
            self.assertNotIn('private-data',str(result))

    async def test_flashx_disables_thinking_for_stream_and_nonstream_and_does_not_drop_it(self):
        processor=LLMProcessor(LLMConfig(provider='glm',api_key='unit-secret',model='glm-4.7-flashx'),CharacterConfig())
        try:
            self.assertEqual(processor._request_kwargs(CallParams())['extra_body'],{'thinking':{'type':'disabled'}})
            with patch.object(processor.client.chat.completions,'create',new=AsyncMock(side_effect=RuntimeError('400 unsupported thinking unit-secret'))) as call:
                with self.assertRaises(LLMError):await processor.chat('hi')
                self.assertEqual(call.await_count,1)
                self.assertEqual(call.call_args.kwargs['extra_body'],{'thinking':{'type':'disabled'}})
                call.reset_mock()
                with self.assertRaises(LLMError):
                    async for _ in processor.chat_stream('hi'):pass
                self.assertEqual(call.await_count,1)
        finally:await processor.close()
