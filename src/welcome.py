"""Bounded arrival batches; prepare speech first and interrupt only eligible modules."""
import asyncio
import random
import re
import time
from collections import OrderedDict
from pathlib import Path
from mutagen.mp3 import MP3


class AudienceWelcomes:
    def __init__(self,pipeline):
        self.pipeline=pipeline;self.pending=OrderedDict();self.seen=OrderedDict();self.started=0;self.last=0;self.overflow=0
        self.task=None

    def offer(self,message):
        cfg=self.pipeline.config.welcome
        if message.msg_type!='enter' or not cfg.enabled:return False
        if self.pipeline.stage.owner=='singing':return True
        now=time.time()
        if now-self.seen.get(message.user_id,0)<cfg.repeat_cooldown:return True
        if not self.pending:self.started=now
        if len(self.pending)<1000 or message.user_id in self.pending:self.pending[message.user_id]=message
        else:self.overflow+=1
        return True

    def maybe_flush(self,now=None):
        now=time.time() if now is None else now;cfg=self.pipeline.config.welcome
        if self.pipeline.stage.owner=='singing':self.pending.clear();self.overflow=0;return
        if (not self.pending or self.task and not self.task.done() or self.pipeline._is_replying
                or now-self.started<cfg.window or now-self.last<cfg.min_interval):return
        batch=list(self.pending.values());overflow=self.overflow;self.pending.clear();self.overflow=0;self.last=now
        for message in batch:
            self.seen[message.user_id]=now;self.seen.move_to_end(message.user_id)
        while len(self.seen)>4000:self.seen.popitem(last=False)
        self.task=self.pipeline._spawn(self._deliver(batch,overflow))

    async def _deliver(self,batch,overflow):
        selected=random.sample(batch,min(len(batch),self.pipeline.config.welcome.max_names))
        names=[]
        for message in selected:
            if self.pipeline.safety.allows_input(message.content) and self.pipeline.safety.allows_input(message.user_name):
                await self.pipeline._emit(type='danmaku',user_name=message.user_name,content=message.content,msg_type='enter')
            name=re.sub(r'[^\w\u4e00-\u9fff -]','',message.user_name)[:12].strip()
            if name and self.pipeline.safety.allows_input(name):names.append(name)
        count=len(batch)+overflow
        text=('欢迎'+ '、'.join(names)+'，' if names else '欢迎新来的朋友，')
        text+=('还有刚进来的朋友们，' if count>len(names) else '')+'随便坐呀。'
        if not await self.pipeline.safety.approve(text):
            await self.pipeline._emit(type='moderation',stage='welcome')
            return
        audio_url=None;duration=2;engine=self.pipeline.tts
        if engine:
            audio_url=await engine.synthesize(text,'happy')
            if engine is not self.pipeline.tts:return
            if audio_url:
                try:duration=min(60,MP3(engine.output_dir/Path(audio_url).name).info.length)
                except (OSError,ValueError):audio_url=None
        if self.pipeline.stage.owner=='singing':return
        service=self.pipeline.performance_resolver(self.pipeline.stage.owner) if self.pipeline.stage.owner else None
        if service:
            if hasattr(service,'welcome'):
                await service.welcome({'text':text,'audio_url':audio_url,'duration':duration,'audience_count':count})
            return
        async with self.pipeline._reply_slot() as acquired:
            if acquired:
                await self.pipeline._emit(type='response',text=text,emotion='happy',audio_url=audio_url,source='welcome',audience_count=count,
                                          action=self.pipeline.action_mapper.get_action('happy'))

    async def run(self):
        try:
            while self.pipeline._running:
                await asyncio.sleep(.25);self.maybe_flush()
        finally:
            if self.task and not self.task.done():self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
