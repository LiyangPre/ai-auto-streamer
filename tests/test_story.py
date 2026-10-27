import asyncio,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import lameenc
from fastapi.testclient import TestClient
from src.config import Config
from src.pipeline import Pipeline
from src.story import StoryStudio
from src.story_catalog import STORIES
from src.danmaku import DanmakuMessage
import src.server as server

def mp3():
    encoder=lameenc.Encoder();encoder.set_bit_rate(96);encoder.set_in_sample_rate(24000);encoder.set_channels(1)
    return bytes(encoder.encode(b'\0'*48000)+encoder.flush())

class StoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.pipeline=Pipeline(Config());self.pipeline._emit=AsyncMock()
        self.studio=StoryStudio(self.tmp.name,self.pipeline)
    async def asyncTearDown(self):
        if self.pipeline.welcomes.task and not self.pipeline.welcomes.task.done():self.pipeline.welcomes.task.cancel();await asyncio.gather(self.pipeline.welcomes.task,return_exceptions=True)
        await self.studio.close();self.tmp.cleanup()

    async def test_prepares_ten_whole_tagged_stories_once_and_randomizes_without_repeating(self):
        calls=[]
        async def chunks(client,text,config,key,**kwargs):
            calls.append(text);self.assertEqual(config.mimo_model,'mimo-v2.5-tts-voicedesign');self.assertEqual(config.mimo_style,cfg.mimo_style);yield mp3()
        cfg=self.pipeline.config.tts;cfg._api_keys={'mimo-tts':'unit-test'}
        with patch('src.story.mimo_audio_chunks',chunks):
            self.studio.prepare(cfg,'astra');await self.studio.task
            self.assertEqual(self.studio.job['completed'],10);self.assertEqual(set(calls),{story['text'] for story in STORIES})
            self.studio.prepare(cfg,'astra');await self.studio.task;self.assertEqual(len(calls),10)
        picked=[self.studio.pick('astra',cfg)['story_id'] for _ in range(10)]
        self.assertEqual(len(set(picked)),10);self.assertNotEqual(self.studio.pick('astra',cfg)['story_id'],picked[-1])

    async def test_story_uses_exact_full_profile_voice_and_rejects_old_voice_cache(self):
        cfg=self.pipeline.config.tts
        cfg.mimo_style='大小姐，自然柔和的高音声线，温暖、轻松、亲切。'+'自然聊天。'*110
        original=cfg.model_copy(deep=True)
        for entry in STORIES:
            prepared=self.studio.voice_config(cfg,entry)
            self.assertEqual(prepared.mimo_style,cfg.mimo_style)
            self.assertGreater(len(prepared.mimo_style),550)
        self.assertEqual(cfg,original)
        entry=STORIES[0];token=self.studio.token('astra',cfg,entry)
        root=Path(self.tmp.name)/'astra';root.mkdir()
        metadata={'id':token,'story_id':entry['id'],'title':entry['title'],'duration':1}
        (root/(token+'.mp3')).write_bytes(mp3())
        (root/(token+'.json')).write_text(json.dumps(metadata),encoding='utf-8')
        self.assertFalse(self.studio.catalog('astra',cfg)[0]['ready'])
        metadata['voice_signature']=self.studio.voice_signature(cfg)
        (root/(token+'.json')).write_text(json.dumps(metadata),encoding='utf-8')
        self.assertTrue(self.studio.catalog('astra',cfg)[0]['ready'])
        cfg.mimo_style+='更轻快。'
        self.assertFalse(self.studio.catalog('astra',cfg)[0]['ready'])

    async def test_welcome_is_prepared_before_pause_then_resumes_unfinished_story_and_finishes_to_chat(self):
        clip={'id':'a'*32,'title':'Story','duration':40,'url':'/api/stories/audio/astra/'+'a'*32}
        await self.studio.play({'vocal_delay':0},clip);self.pipeline.performance_resolver=lambda owner:self.studio
        voice=Path(self.tmp.name)/'tts_welcome.mp3';voice.write_bytes(mp3());gate=asyncio.Event()
        async def synth(*args):await gate.wait();return '/audio/tts_welcome.mp3'
        self.pipeline.tts=SimpleNamespace(synthesize=synth,output_dir=Path(self.tmp.name))
        now=time.time()
        for uid in range(7):self.pipeline.danmaku_queue.offer(DanmakuMessage('',f'观众{uid}',uid,now,msg_type='enter'))
        self.pipeline.welcomes.maybe_flush(now+20);await asyncio.sleep(0)
        self.assertFalse(self.studio.playback.get('paused',False));self.assertTrue(self.pipeline.danmaku_queue.empty())
        gate.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if self.studio.playback.get('paused'):break
        p=self.studio.playback;self.assertTrue(p['paused']);self.assertEqual(p['welcome']['audience_count'],7)
        self.assertLessEqual(p['welcome']['text'].count('观众'),3)
        await self.studio.complete(p['id']);self.assertIsNotNone(self.studio.playback)
        await self.studio.resume_welcome(p['id'],p['welcome']['id']);await self.pipeline.welcomes.task
        self.assertFalse(p['paused']);self.assertEqual(self.pipeline.stage.owner,'story')
        await self.studio.complete(p['id']);self.assertIsNone(self.pipeline.stage.owner)
        self.assertEqual(self.pipeline._emit.call_args.kwargs['return_to'],'chat')

    async def test_singing_arrivals_are_suppressed_and_never_deferred_to_story(self):
        self.pipeline.stage.acquire('singing')
        for uid in range(20):self.pipeline.danmaku_queue.offer(DanmakuMessage('',str(uid),uid,time.time(),msg_type='enter'))
        self.assertFalse(self.pipeline.welcomes.pending);self.assertTrue(self.pipeline.danmaku_queue.empty())

class StoryAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();cfg=Config();cfg.llm.provider='mock';cfg.tts.engine='none';cfg.idle.enabled=False
        self.patches=[patch('src.server.Config.load',return_value=cfg),patch('src.server.settings_path',return_value=Path(self.tmp.name)/'studio-settings.json'),patch('src.danmaku.sys.stdin.isatty',return_value=False)]
        for item in self.patches:item.start()
        self.client=TestClient(server.app,headers={'X-Studio-API':'1','Origin':'http://testserver'});self.client.__enter__()
        self.studio=server.app.state.modules.service('story');self.cfg=cfg
        root=self.studio.root/'astra';root.mkdir(parents=True);entry=STORIES[0];token=self.studio.token('astra',cfg.tts,entry)
        (root/(token+'.json')).write_text(json.dumps({'id':token,'story_id':entry['id'],'profile_id':'astra','title':entry['title'],'duration':40,'voice_signature':self.studio.voice_signature(cfg.tts),'url':f'/api/stories/audio/astra/{token}'}))
        (root/(token+'.mp3')).write_bytes(mp3())
    def tearDown(self):
        self.client.__exit__(None,None,None)
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()
    def test_viewer_trigger_stop_and_profile_policy_are_local_and_reuse_prepared_audio(self):
        data=self.client.get('/api/stories').json();self.assertEqual(sum(story['ready'] for story in data['stories']),1)
        self.assertFalse(data['policy']['auto_idle'])
        self.assertEqual(self.client.post('/api/danmaku',json={'content':'讲个故事'}).status_code,200)
        self.assertEqual(self.client.get('/api/status').json()['active_module'],'story')
        self.client.post('/api/danmaku',json={'content':'停止故事'})
        self.assertEqual(self.client.get('/api/status').json()['active_module'],'chat')
        data=self.client.get('/api/stories').json();data['policy']['chat_requests']=False
        self.assertEqual(self.client.post('/api/stories/settings',json={'profile_id':'astra','revision':data['revision'],'policy':data['policy']}).status_code,200)
        self.assertEqual(self.client.get('/api/stories',headers={'Origin':'https://elsewhere.example'}).status_code,403)
