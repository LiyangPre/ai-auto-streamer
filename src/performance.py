"""Finite modules serialize transitions and release the stage back to chat."""
import asyncio
import time
import uuid
import math


class PerformanceSession:
    def __init__(self,pipeline,module_id):
        self.pipeline=pipeline;self.module_id=module_id;self.timer=None;self.playback=None;self.welcome_timer=None
        self.welcome_done=asyncio.Event();self.welcome_done.set();self._transition_lock=asyncio.Lock()

    def snapshot(self):return self.playback

    async def _cancel_timer(self,name):
        task=getattr(self,name);setattr(self,name,None)
        if task and task is not asyncio.current_task():
            task.cancel();await asyncio.gather(task,return_exceptions=True)

    def _arm_finish(self,playback):
        async def finish():
            remaining=playback.get('play_duration',playback['song']['duration'])+max(0,playback.get('vocal_delay',0))+playback['starts_at']-time.time()
            await asyncio.sleep(max(0,remaining)+.5)
            await self.complete(playback['id'])
        self.timer=asyncio.create_task(finish())

    async def play(self,data,clip):
        async with self._transition_lock:
            await self._stop_locked()
            self.pipeline.stage.acquire(self.module_id)
            now=time.time();data=dict(data);intro=data.get('intro')
            lead=3.
            if isinstance(intro,dict):
                duration=intro.get('duration')
                if type(duration) in (int,float) and math.isfinite(duration) and 0<duration<=12:
                    data['intro']={**intro,'starts_at':now+.15}
                    lead=max(lead,duration+.35)
                else:data.pop('intro',None)
            self.playback={**data,'module_id':self.module_id,'id':uuid.uuid4().hex,'song':clip,'starts_at':now+lead}
            await self.pipeline._emit(type=self.module_id+'_start',performance=self.playback)
            self._arm_finish(self.playback)

    async def prepare_transition_audio(self):
        transitions=getattr(self.pipeline,'transitions',None)
        if not transitions:return {}
        pair=await transitions.prepare_pair(self.module_id)
        return {name:clip for name,clip in pair.items() if clip and transitions.matches_current(clip)}

    async def welcome(self,greeting):
        async with self._transition_lock:
            if self.module_id=='singing' or not self.playback or self.playback.get('paused'):return
            playback=self.playback
            await self._cancel_timer('timer')
            token=uuid.uuid4().hex;self.welcome_done.clear()
            playback.update(paused=True,paused_at=time.time(),welcome={**greeting,'id':token,'starts_at':time.time()+.15})
            await self.pipeline._emit(type='performance_welcome',module_id=self.module_id,playback_id=playback['id'],greeting=playback['welcome'])
            async def fallback():
                await asyncio.sleep(greeting['duration']+3)
                await self.resume_welcome(playback['id'],token)
            self.welcome_timer=asyncio.create_task(fallback())
        await self.welcome_done.wait()

    async def resume_welcome(self,playback_id,welcome_id,from_timer=False):
        async with self._transition_lock:
            p=self.playback
            if not p or p['id']!=playback_id or p.get('welcome',{}).get('id')!=welcome_id:return
            await self._cancel_timer('welcome_timer')
            p['starts_at']+=time.time()-p['paused_at'];p.pop('paused_at',None);p.pop('welcome',None);p['paused']=False
            await self.pipeline._emit(type='performance_resume',module_id=self.module_id,performance=p)
            self._arm_finish(p);self.welcome_done.set()

    async def _stop_locked(self,transition=None):
        await self._cancel_timer('welcome_timer');await self._cancel_timer('timer')
        playback=self.playback;had_playback=playback is not None;self.playback=None;self.pipeline.stage.release(self.module_id)
        self.pipeline._last_activity_time=time.time()
        if had_playback:
            self.pipeline._last_performance_end_time=time.time()
            payload={'type':self.module_id+'_stop','return_to':'chat','playback_id':playback['id']}
            if transition:payload['transition']=transition
            await self.pipeline._emit(**payload)
        self.welcome_done.set()

    async def stop(self,from_timer=False):
        async with self._transition_lock:await self._stop_locked()

    async def complete(self,playback_id,reason='ended'):
        async with self._transition_lock:
            if self.playback and self.playback['id']==playback_id and not self.playback.get('paused'):
                outro=self.playback.get('outro') if reason=='ended' else None
                transitions=getattr(self.pipeline,'transitions',None)
                if outro and transitions and not transitions.matches_current(outro):outro=None
                await self._stop_locked(transition=outro)

    async def close(self):await self.stop()
