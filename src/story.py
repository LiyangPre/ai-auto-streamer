"""Prepared expressive stories: one voice-design request per full story, local replay."""
import asyncio
import hashlib
import json
import random
import time
from pathlib import Path

from mutagen.mp3 import MP3
from .performance import PerformanceSession
from .provider_network import http_client, failure
from .mimo_tts import mimo_audio_chunks
from .story_catalog import STORIES
from .story_emotions import story_expression_cues
from pydantic import BaseModel,ConfigDict,Field

VOICE_PROMPT_VERSION = 'profile-voice-v2'


class StoryPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    chat_requests:bool=True
    request_cooldown:float=Field(default=45,ge=5,le=600)
    auto_idle:bool=False
    idle_seconds:float=Field(default=120,ge=30,le=3600)
    auto_interval:float=Field(default=600,ge=120,le=7200)


def stories_directory(studio_path):
    path=Path(studio_path)
    name=path.stem.replace('studio-settings','stories')
    return path.with_name(name if name!=path.stem else name+'-stories')


class StoryStudio(PerformanceSession):
    def __init__(self,root,pipeline,state=None):
        super().__init__(pipeline,'story')
        self.root=Path(root);self.task=None;self.job={'state':'idle'};self.bags={};self.last={}
        self.state=state;self.trigger_task=None;self.command_task=None;self.last_request=0;self.last_auto=0

    def policy(self):
        profile=self.state.characters.profiles[self.state.active_character]
        return StoryPolicy.model_validate(profile.module_options.get('story',{}))

    async def start(self):
        self.pipeline.ingress_handlers.append(self.on_message)
        self.trigger_task=asyncio.create_task(self.pipeline._supervise('story-idle',self._idle_triggers))

    def on_message(self,message):
        if message.msg_type!='danmaku' or not self.policy().chat_requests or self.pipeline.stage.owner=='singing':return False
        import re
        text=re.sub(r'^(?:Astra|阿斯特拉|星星)[，,：: ]*','',message.content.strip(),flags=re.I)
        text=text.strip('？?！!。~～ ')
        if text in {'停止故事','结束故事','别讲了'}:command='stop'
        elif text in {'继续故事','接着讲'}:return self.pipeline.stage.owner=='story'
        elif text in {'换个故事','换一个故事'}:command='change'
        elif text in {'讲个故事','讲一个故事','来个故事','随机故事','能讲个故事吗','可以讲个故事吗'}:command='play'
        else:return False
        if not self.pipeline.safety.allows_input(message.content):return True
        if self.command_task and not self.command_task.done():return True
        if command=='play' and self.pipeline.stage.owner=='story':return True
        if command!='stop' and time.time()-self.last_request<self.policy().request_cooldown:return True
        if command!='stop':self.last_request=time.time()
        async def run():
            try:
                if command=='stop':
                    async with self.state.settings_lock:await self.stop()
                else:await self.activate()
            except ValueError as error:await self.pipeline._emit(type='module_notice',module_id='story',message=str(error))
        self.command_task=asyncio.create_task(run());return True

    async def activate(self,story_id=None,music='shared',vocal_volume=.85):
        state=self.state
        profile_id=state.active_character
        signature=self.voice_signature(state.runtime_config.tts)
        if self.pipeline.stage.owner not in (None,'story') or self.pipeline._is_replying:
            raise ValueError('请先结束当前表演或等待回复准备完成。')
        clip=self.pick(profile_id,state.runtime_config.tts,story_id)
        transition_audio=await self.prepare_transition_audio()
        async with state.settings_lock:
            if state.active_character!=profile_id or self.voice_signature(state.runtime_config.tts)!=signature:
                raise ValueError('角色或声音设置已改变，请重新选择故事。')
            if self.pipeline.stage.owner not in (None,'story') or self.pipeline._is_replying or any(not t.done() for t in self.pipeline._background_tasks):
                raise ValueError('请先结束当前表演或等待回复准备完成。')
            if self.pipeline.tts and any(not job.done for job in self.pipeline.tts.jobs.values()):raise ValueError('语音正在准备，请稍后再试。')
            payload={'music':music,'vocal_volume':vocal_volume,'presentation':state.modules.presentation('story'),
                     'backing_url':None,'backing_volume':0,'vocal_delay':0,'bpm':60}
            transitions=getattr(self.pipeline,'transitions',None)
            payload.update({name:clip for name,clip in transition_audio.items()
                            if not transitions or transitions.matches_current(clip)})
            async with self.pipeline._reply_lock:await self.play(payload,clip)

    async def _idle_triggers(self):
        while True:
            await asyncio.sleep(1);policy=self.policy();now=time.time()
            if (not policy.auto_idle or self.pipeline.performance_active or self.pipeline._is_replying or not self.pipeline.ws_clients
                    or (getattr(self.pipeline, 'interactions', None) and self.pipeline.interactions.engaged)
                    or not self.pipeline.danmaku_queue.empty() or self.pipeline.welcomes.pending
                    or self.pipeline.paid.items or self.pipeline._paid_batch
                    or now-max(self.pipeline._last_viewer_time,self.pipeline._last_performance_end_time)<policy.idle_seconds or now-self.last_auto<policy.auto_interval):continue
            self.last_auto=now
            try:await self.activate()
            except ValueError:pass

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def module_status(self):
        return 'Telling a story' if self.playback else 'Preparing stories' if self.busy else 'Ready'

    def voice_config(self,config,story):
        cfg=config.model_copy(deep=True);cfg.mimo_model='mimo-v2.5-tts-voicedesign';cfg.timeout=240
        # The user message designs the speaker, rather than just directing delivery.
        # Keep the exact profile prompt used by chat/auditions; acting tags already
        # live in the assistant's story text and must not redefine the voice here.
        cfg.mimo_style=config.mimo_style
        return cfg

    def voice_signature(self,config):
        return hashlib.sha256(json.dumps([VOICE_PROMPT_VERSION,'mimo-v2.5-tts-voicedesign',config.mimo_style],ensure_ascii=False).encode()).hexdigest()

    def token(self,profile_id,config,story):
        return hashlib.sha256(json.dumps([profile_id,self.voice_signature(config),story['text']],ensure_ascii=False).encode()).hexdigest()[:32]

    def catalog(self,profile_id,config):
        result=[]
        for story in STORIES:
            token=self.token(profile_id,config,story);path=self.root/profile_id/(token+'.json')
            clip=None
            try:
                clip=json.loads(path.read_text(encoding='utf-8'))
                if (clip['id']!=token or clip.get('voice_signature')!=self.voice_signature(config)
                        or not path.with_suffix('.mp3').is_file()):clip=None
            except (OSError,ValueError,KeyError):pass
            if clip:
                clip={**clip,'expression_cues':story_expression_cues(story['text'],clip.get('duration',0)),
                      'emotion_timing':'text-proportional'}
            result.append({**story,'ready':clip is not None,'clip':clip})
        return result

    def public(self,profile_id,config):
        return {'job':self.job,'stories':self.catalog(profile_id,config),'playback':self.playback,
                'profile_id':profile_id,'model':'mimo-v2.5-tts-voicedesign',
                'voice':{'source':'character-profile','description':config.mimo_style,'signature':self.voice_signature(config)}}

    def prepare(self,config,profile_id):
        if self.busy:raise ValueError('故事正在准备，请稍等或取消。')
        if not config.resolve_api_key('mimo-tts'):raise ValueError('请先保存 MiMo API 密钥。')
        cfg=config.model_copy(deep=True)
        self.job={'state':'preparing','profile_id':profile_id,'completed':0,'total':len(STORIES),'started':time.time(),'errors':[]}
        self.task=asyncio.create_task(self._prepare(cfg,profile_id));return self.job

    async def _prepare(self,config,profile_id):
        root=self.root/profile_id
        try:
            root.mkdir(parents=True,exist_ok=True)
            for entry in self.catalog(profile_id,config):
                self.job['title']=entry['title']
                if entry['ready']:
                    self.job['completed']+=1;continue
                token=self.token(profile_id,config,entry);temporary=root/(token+'.part');started=time.monotonic();first=None
                try:
                    cfg=self.voice_config(config,entry)
                    async def generate():
                        nonlocal first
                        size=0
                        async with http_client(cfg.mimo_network_mode,240) as client:
                            with temporary.open('wb') as output:
                                async for chunk in mimo_audio_chunks(client,entry['text'],cfg,cfg.resolve_api_key('mimo-tts'),max_pcm_bytes=48000*300):
                                    if first is None:first=round((time.monotonic()-started)*1000)
                                    size+=len(chunk)
                                    if size>8*1024*1024:raise ValueError('Story audio too large')
                                    output.write(chunk)
                        return size
                    size=await asyncio.wait_for(generate(),240)
                    duration=MP3(temporary).info.length
                    if not 0<duration<=300:raise ValueError('Invalid story duration')
                    clip={'id':token,'story_id':entry['id'],'profile_id':profile_id,'title':entry['title'],
                          'duration':round(duration,3),'bytes':size,'model':cfg.mimo_model,'first_audio_ms':first,
                          'voice_signature':self.voice_signature(config),'voice_prompt_version':VOICE_PROMPT_VERSION,
                          'voice_style':cfg.mimo_style,
                          'generation_ms':round((time.monotonic()-started)*1000),'created':time.time(),
                          'expression_cues':story_expression_cues(entry['text'],duration),'emotion_timing':'text-proportional',
                          'url':f'/api/stories/audio/{profile_id}/{token}'}
                    metadata=root/(token+'.json.tmp');metadata.write_text(json.dumps(clip,ensure_ascii=False,indent=2),encoding='utf-8')
                    temporary.replace(root/(token+'.mp3'));metadata.replace(root/(token+'.json'))
                    self.job['completed']+=1
                except asyncio.CancelledError:raise
                except Exception as error:self.job['errors'].append({'story_id':entry['id'],**failure(error)})
                finally:
                    temporary.unlink(missing_ok=True);(root/(token+'.json.tmp')).unlink(missing_ok=True)
            self.job['state']='ready' if self.job['completed']==len(STORIES) else 'partial'
        except asyncio.CancelledError:
            self.job['state']='cancelled';raise
        except Exception as error:self.job.update(state='error',**failure(error))

    def pick(self,profile_id,config,story_id=None):
        ready={entry['id']:entry['clip'] for entry in self.catalog(profile_id,config) if entry['ready']}
        if story_id:
            if story_id not in ready:raise ValueError('这个故事尚未准备好，请先生成人声。')
            choice=story_id
        else:
            if not ready:raise ValueError('请先准备故事音频，然后再随机播放。')
            bag=[key for key in self.bags.get(profile_id,[]) if key in ready]
            if not bag:
                bag=list(ready);random.shuffle(bag)
                if len(bag)>1 and bag[-1]==self.last.get(profile_id):bag[0],bag[-1]=bag[-1],bag[0]
            choice=bag.pop();self.bags[profile_id]=bag
        self.last[profile_id]=choice
        return ready[choice]

    async def close(self):
        if self.on_message in self.pipeline.ingress_handlers:self.pipeline.ingress_handlers.remove(self.on_message)
        tasks=[task for task in (self.trigger_task,self.command_task,self.task) if task and not task.done()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if self.busy:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        await super().close()
