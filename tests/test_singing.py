import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import lameenc
from fastapi.testclient import TestClient

from src.config import Config, TTSConfig
from src.singing import SingingStudio, SongDraft, SongPlay, SongPlaybackRange
from src.pipeline import Pipeline
import src.server as server


def mp3():
    encoder=lameenc.Encoder();encoder.set_bit_rate(96);encoder.set_in_sample_rate(24000);encoder.set_channels(1)
    return bytes(encoder.encode(b'\0'*48000)+encoder.flush())


class SingingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.pipeline=Pipeline(Config())
        self.pipeline._emit=AsyncMock()
        self.studio=SingingStudio(self.directory.name,self.pipeline)

    async def asyncTearDown(self):
        await self.studio.close();self.directory.cleanup()

    async def test_segment_timer_uses_selected_duration_without_changing_recording(self):
        original_sleep=asyncio.sleep
        song={'duration':300,'playback_range':{'start_seconds':60,'end_seconds':90}}
        with patch('src.performance.time.time',return_value=1000), patch(
                'src.performance.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
            await self.studio.play({'vocal_delay':2},song)
            await original_sleep(0)
            self.assertEqual(self.studio.playback['start_offset'],60)
            self.assertEqual(self.studio.playback['play_duration'],30)
            self.assertEqual(self.studio.playback['song']['duration'],300)
            sleep.assert_awaited_once_with(35.5)

    async def test_one_tagged_request_complete_file_and_shared_clock_playback_state(self):
        lyrics='星星在窗外，晚风轻轻来。\n银龙唱首歌，好梦等你来。'
        async def chunks(client,text,config,key,**kwargs):
            self.assertEqual(text,'(唱歌)'+lyrics)
            self.assertEqual(key,'test-sing-key')
            self.assertEqual(kwargs['max_pcm_bytes'],48000*300)
            self.assertEqual(config.timeout,240)
            yield mp3()
        config=TTSConfig();config._api_keys={'mimo-tts':'test-sing-key'}
        with patch('src.singing.mimo_audio_chunks',chunks):
            self.studio.prepare(SongDraft(lyrics=lyrics),config,'astra');await self.studio.task
        self.assertEqual(self.studio.job['state'],'ready')
        song=self.studio.catalog()[0]
        self.assertGreater(song['duration'],.9)
        self.assertNotIn('test-sing-key',json.dumps(song))
        self.assertEqual(len(SingingStudio(self.directory.name,self.pipeline).catalog()),1)
        await self.studio.play(SongPlay(song_id=song['id']).model_dump(),song)
        self.assertTrue(self.pipeline.performance_active);self.assertFalse(self.pipeline.performance_idle.is_set())
        self.assertEqual(self.pipeline._emit.call_args.kwargs['type'],'singing_start')
        await self.studio.stop()
        self.assertFalse(self.pipeline.performance_active);self.assertTrue(self.pipeline.performance_idle.is_set())
        self.assertEqual(self.pipeline._emit.call_args.kwargs['type'],'singing_stop')

    async def test_failure_or_cancel_never_publishes_partial_song_or_provider_secrets(self):
        async def broken(*args,**kwargs):
            yield b'partial audio'
            raise httpx.ConnectError('secret-url-test-key')
        with patch('src.singing.mimo_audio_chunks',broken):
            self.studio.prepare(SongDraft(lyrics='原创歌词'),TTSConfig(),'astra');await self.studio.task
        self.assertEqual(self.studio.job['state'],'error')
        self.assertNotIn('secret-url',str(self.studio.public()))
        self.assertEqual(list(Path(self.directory.name).iterdir()),[])
        gate=asyncio.Event()
        async def waiting(*args,**kwargs):
            yield b'partial';await gate.wait()
        with patch('src.singing.mimo_audio_chunks',waiting):
            self.studio.prepare(SongDraft(lyrics='原创歌词'),TTSConfig(),'astra')
            with self.assertRaises(ValueError):self.studio.prepare(SongDraft(lyrics='另一首'),TTSConfig(),'astra')
            await asyncio.sleep(.01);self.studio.task.cancel();await asyncio.gather(self.studio.task,return_exceptions=True)
        self.assertEqual(self.studio.job['state'],'cancelled')
        self.assertEqual(list(Path(self.directory.name).iterdir()),[])


class SingingAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'studio-settings.json'
        self.config=Config();self.config.llm.provider='mock';self.config.tts.engine='none';self.config.idle.enabled=False
        self.patches=[patch('src.server.Config.load',return_value=self.config),patch('src.server.settings_path',return_value=self.path),
                      patch('src.danmaku.sys.stdin.isatty',return_value=False)]
        for item in self.patches:item.start()
        self.client=TestClient(server.app, base_url='http://localhost', headers={'X-Studio-API':'1','Origin':'http://localhost'});self.client.__enter__()
        self.studio=server.app.state.singing;self.studio.root.mkdir(parents=True,exist_ok=True)
        self.song={'id':'b'*32,'title':'Test','profile_id':'astra','duration':1,'created':0,'voice':'冰糖','bytes':100,
                   'url':'/api/singing/audio/'+'b'*32,'generation_ms':100}
        (self.studio.root/('b'*32+'.json')).write_text(json.dumps(self.song),encoding='utf-8')
        (self.studio.root/('b'*32+'.mp3')).write_bytes(mp3())

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def test_per_song_range_persists_and_edits_apply_to_next_take(self):
        song_id=self.song['id']
        body={'song_id':song_id,'playback_range':{'start_seconds':.1,'end_seconds':.8}}
        self.assertEqual(self.client.post('/api/singing/range',json=body).status_code,200)
        saved=self.studio.catalog()[0]
        self.assertEqual(saved['playback_range'],body['playback_range'])
        self.assertEqual(self.client.post('/api/singing/play',json={'song_id':song_id}).status_code,200)
        self.assertEqual(self.studio.playback['start_offset'],.1)
        self.assertAlmostEqual(self.studio.playback['play_duration'],.7)
        self.assertEqual(self.studio.playback['song']['duration'],1)
        updated={**body,'playback_range':{'start_seconds':.2,'end_seconds':None}}
        self.assertEqual(self.client.post('/api/singing/range',json=updated).status_code,200)
        self.assertEqual(self.studio.playback['start_offset'],.1)
        self.client.post('/api/singing/stop',json={})
        self.client.post('/api/singing/play',json={'song_id':song_id})
        self.assertEqual(self.studio.playback['start_offset'],.2)
        self.assertAlmostEqual(self.studio.playback['play_duration'],.8)
        self.client.post('/api/singing/stop',json={})
        self.assertEqual(self.client.post('/api/singing/range',json={**body,'playback_range':{}}).status_code,200)
        self.client.post('/api/singing/play',json={'song_id':song_id})
        self.assertEqual(self.studio.playback['start_offset'],0)
        self.assertEqual(self.studio.playback['play_duration'],1)

    def test_invalid_or_other_character_range_never_changes_saved_song(self):
        before=(self.studio.root/(self.song['id']+'.json')).read_bytes()
        for selected in ({'start_seconds':-1},{'start_seconds':True},{'end_seconds':True},
                         {'start_seconds':.8,'end_seconds':.4},{'end_seconds':2},
                         {'start_seconds':1},{'start_seconds':.9,'end_seconds':1}):
            with self.subTest(selected=selected):
                result=self.client.post('/api/singing/range',json={'song_id':self.song['id'],'playback_range':selected})
                self.assertEqual(result.status_code,422)
                self.assertEqual((self.studio.root/(self.song['id']+'.json')).read_bytes(),before)
        self.assertEqual(self.client.post('/api/singing/range',json={'song_id':self.song['id'],'playback_range':{}},headers={'Origin':'https://evil.example'}).status_code,403)
        other={**self.song,'id':'c'*32,'profile_id':'other'}
        (self.studio.root/(other['id']+'.json')).write_text(json.dumps(other),encoding='utf-8')
        (self.studio.root/(other['id']+'.mp3')).write_bytes(mp3())
        self.assertEqual(self.client.post('/api/singing/range',json={'song_id':other['id'],'playback_range':{}}).status_code,422)

    def test_stage_controls_are_local_validated_and_resume_chat_on_stop(self):
        body={'song_id':'b'*32}
        self.assertEqual(self.client.post('/api/singing/play',json=body,headers={'Origin':'https://elsewhere.example'}).status_code,403)
        invalid={**body,'music':'accompaniment','backing_id':'c'*32}
        self.assertEqual(self.client.post('/api/singing/play',json=invalid).status_code,422)
        self.assertFalse(server.pipeline.performance_active)
        self.assertEqual(self.client.post('/api/singing/play',json=body).status_code,200)
        self.assertTrue(server.pipeline.performance_active)
        self.assertFalse(server.pipeline._maybe_warmup())
        context=self.client.get('/api/characters').json()
        self.assertEqual(self.client.post('/api/characters/activate',json={'profile_id':'astra','revision':context['revision']}).status_code,409)
        self.assertEqual(self.client.post('/api/direct-reply',json={'text':'不要打断演唱'}).status_code,409)
        self.assertEqual(self.client.post('/api/singing/stop',json={}).status_code,200)
        self.assertFalse(server.pipeline.performance_active)
        self.assertTrue(server.pipeline.performance_idle.is_set())
        self.assertEqual(self.client.get(self.song['url']).status_code,200)
        self.assertEqual(self.client.get('/api/singing/audio/invalid').status_code,404)

    def test_uploaded_recording_and_optional_guide_need_no_tts_and_finish_to_chat(self):
        imported=self.client.post('/api/media/music',content=mp3(),headers={'X-Filename':'song.mp3','Content-Type':'audio/mpeg'})
        self.assertEqual(imported.status_code,200)
        asset_id=imported.json()['asset']['id']
        result=self.client.post('/api/singing/import',json={'asset_id':asset_id});self.assertEqual(result.status_code,200,result.text)
        song=result.json();self.assertEqual(song['source'],'upload');self.assertEqual(song['profile_id'],'astra')
        guide=self.client.post('/api/singing/import',json={'asset_id':asset_id,'song_id':song['id']});self.assertEqual(guide.status_code,200)
        self.assertEqual(self.client.get(guide.json()['guide_url']).status_code,200)
        self.assertEqual(self.client.post('/api/singing/play',json={'song_id':song['id']}).status_code,200)
        playback=self.studio.playback
        completed=self.client.post('/api/modules/singing/complete',json={'playback_id':playback['id']})
        self.assertEqual(completed.json()['active_module'],'chat');self.assertFalse(server.pipeline.performance_active)
