"""Local-only credential editing and bounded, non-broadcast connection checks."""
import asyncio
import time

from fastapi import APIRouter, HTTPException, Request

from .api_settings import ConnectionUpdate, ConnectionTest
from .llm import LLMProcessor, MockLLM, CallParams, LLMError
from .provider_network import failure, route
from .tts import TTSEngine

router=APIRouter(prefix='/api/connections')


def local_request(request):
    if not request.client or request.client.host not in {'127.0.0.1','::1','testclient'}:
        raise HTTPException(403,'API settings can only be edited on this computer.')
    allowed_hosts={'127.0.0.1','localhost','::1'}
    if request.client.host=='testclient':
        allowed_hosts.add('testserver')
    if request.url.hostname not in allowed_hosts:
        raise HTTPException(403,'Open API settings using localhost or 127.0.0.1.')
    expected=str(request.base_url).rstrip('/')
    if (request.headers.get('origin') not in (None,expected)
            or request.headers.get('sec-fetch-site') not in (None,'same-origin','none')
            or request.headers.get('x-studio-api') != '1'):
        raise HTTPException(403,'Open API settings from the local controller.')


@router.get('')
async def read_connections(request: Request):
    local_request(request)
    state=request.app.state
    return state.connections.public(state.runtime_config)


@router.post('')
async def save_connections(data: ConnectionUpdate, request: Request):
    local_request(request)
    state=request.app.state;pipeline=state.pipeline;config=state.runtime_config
    async with state.settings_lock:
        if data.revision != state.connections.revision:
            raise HTTPException(409,'API settings changed in another window. Reload this panel before saving.')
        if (pipeline.performance_active or pipeline._is_replying or any(not task.done() for task in pipeline._background_tasks)
                or state.preview_lock.locked() or (pipeline.tts and any(not job.done for job in pipeline.tts.jobs.values()))):
            raise HTTPException(409,'A reply or voice is being prepared. Wait for it to finish, then save again.')
        async with pipeline._reply_lock, state.preview_lock:
            candidate=state.connections.candidate(data)
            updated=config.model_copy(deep=True);candidate.apply(updated)
            new_llm=MockLLM(config.character) if updated.llm.provider=='mock' else LLMProcessor(updated.llm,config.character)
            try:
                candidate.save()
            except OSError:
                if isinstance(new_llm,LLMProcessor):
                    await new_llm.close()
                raise HTTPException(500,'Cannot save local API settings. Current settings were retained.') from None
            old_llm=pipeline.llm
            config.llm=updated.llm;config.tts=updated.tts
            pipeline.llm=new_llm
            if pipeline.tts:
                await pipeline.tts.close()
            pipeline.tts=TTSEngine(config.tts) if config.tts.engine!='none' else None
            await state.preview_tts.close()
            state.preview_tts=TTSEngine(config.tts.model_copy(deep=True))
            state.connections=candidate
            if isinstance(old_llm,LLMProcessor):
                await old_llm.close()
            # No keys or credential status enter the stream event channel.
            await pipeline._emit(type='api_connections_updated')
            return candidate.public(config)


@router.post('/test')
async def test_connection(data: ConnectionTest, request: Request):
    local_request(request)
    state=request.app.state
    if state.connection_test_lock.locked():
        raise HTTPException(429,'A connection test is already running. Please wait.')
    async with state.connection_test_lock:
        # Test the draft without persisting keys or changing the live pipeline.
        async with state.settings_lock:
            candidate=state.connections.candidate(data)
            config=state.runtime_config.model_copy(deep=True);candidate.apply(config)
        started=time.monotonic();engine=None
        result={'ok':False,'target':data.target}
        try:
            if data.target=='text':
                result['route']=route(config.llm.network_mode)[0]
                if config.llm.provider not in {'mock','ollama'} and not config.llm.api_key_configured:
                    return {**result,'code':'missing_key','message':'Enter a text API key, or configure it in .env.'}
                engine=MockLLM(config.character) if config.llm.provider=='mock' else LLMProcessor(config.llm,config.character)
                await asyncio.wait_for(engine.chat('只回复：连接成功。',CallParams(max_tokens=32)),25)
                result.update(ok=True,message='Text reply received · 文本连接成功',model=config.llm.model,thinking=False)
            else:
                config.tts.engine=data.target
                if data.target=='mimo-tts':result['model']=config.tts.mimo_model
                result['route']=route(config.tts.mimo_network_mode)[0] if data.target=='mimo-tts' else 'environment'
                if not config.tts.resolve_api_key():
                    return {**result,'code':'missing_key','message':'Enter an API key for this voice provider.'}
                engine=TTSEngine(config.tts)
                async def synthesize():
                    total=0
                    async for chunk in engine._audio_chunks('你好，连接成功。',engine._voice_params(None),config.tts,None):
                        if not total:result['first_audio_ms']=round((time.monotonic()-started)*1000)
                        total+=len(chunk)
                        if total>2*1024*1024:raise ValueError('Audio too large')
                    if not total:raise ValueError('Empty audio')
                    return total
                size=await asyncio.wait_for(synthesize(),25)
                result.update(ok=True,message='Voice audio received · 语音连接成功（未播放）',audio_bytes=size)
        except LLMError as error:
            # LLMError contains only our own sanitized diagnostic.
            result.update(code='text_error',message=str(error))
        except Exception as error:
            result.update(failure(error))
        finally:
            if engine and hasattr(engine,'close'):await engine.close()
        result['elapsed_ms']=round((time.monotonic()-started)*1000)
        return result
