import asyncio
import re
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from .api_routes import local_request
from .story import StoryPolicy
from .characters import save_library

router=APIRouter(prefix='/api/stories')


class StoryPlay(BaseModel):
    model_config=ConfigDict(extra='forbid')
    story_id:str|None=Field(default=None,max_length=48)
    music:Literal['quiet','shared']='shared'
    vocal_volume:float=Field(default=.85,ge=0,le=1,allow_inf_nan=False)


class PolicyUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile_id:str=Field(max_length=48)
    revision:int=Field(ge=0)
    policy:StoryPolicy


@router.get('')
async def get_stories(request:Request):
    local_request(request);state=request.app.state
    studio=state.modules.service('story')
    return {**studio.public(state.active_character,state.runtime_config.tts),'policy':studio.policy().model_dump(),
            'revision':state.characters.revision,'welcome':state.runtime_config.welcome.model_dump()}


@router.post('/settings')
async def settings(data:PolicyUpdate,request:Request):
    local_request(request);state=request.app.state
    async with state.settings_lock:
        if data.profile_id!=state.active_character or data.revision!=state.characters.revision:raise HTTPException(409,'角色设置已改变，请重新打开。')
        library=state.characters.model_copy(deep=True);library.profiles[data.profile_id].module_options['story']=data.policy.model_dump();library.revision+=1
        try:save_library(library,state.character_path)
        except OSError:raise HTTPException(500,'无法保存故事触发设置。') from None
        state.characters=library
        await state.pipeline._emit(type='module_settings_updated',modules=state.modules.public(),profile_context=library.context(state.active_character))
    return await get_stories(request)


@router.post('/prepare',status_code=202)
async def prepare(request:Request):
    local_request(request);state=request.app.state
    async with state.settings_lock:
        try:return state.modules.service('story').prepare(state.runtime_config.tts,state.active_character)
        except ValueError as error:raise HTTPException(409,str(error)) from None


@router.post('/cancel')
async def cancel(request:Request):
    local_request(request);studio=request.app.state.modules.service('story')
    if studio.busy:studio.task.cancel();await asyncio.gather(studio.task,return_exceptions=True)
    return await get_stories(request)


@router.post('/play')
async def play(data:StoryPlay,request:Request):
    local_request(request);state=request.app.state
    try:await state.modules.service('story').activate(data.story_id,data.music,data.vocal_volume)
    except ValueError as error:raise HTTPException(409,str(error)) from None
    return await get_stories(request)


@router.post('/stop')
async def stop(request:Request):
    local_request(request)
    async with request.app.state.settings_lock:await request.app.state.modules.service('story').stop()
    return await get_stories(request)


@router.get('/audio/{profile_id}/{token}')
async def audio(profile_id:str,token:str,request:Request):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}',profile_id) or not re.fullmatch(r'[a-f0-9]{32}',token):raise HTTPException(404)
    path=request.app.state.modules.service('story').root/profile_id/(token+'.mp3')
    if not path.is_file() or not path.with_suffix('.json').is_file():raise HTTPException(404)
    return FileResponse(path,media_type='audio/mpeg')
