"""Shared module discovery and character-owned presentation settings."""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from ..local_security import local_request
from ..characters import ModulePresentation, save_library

router = APIRouter(prefix='/api/modules')


class PresentationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str = Field(max_length=48)
    revision: int = Field(ge=0)
    presentation: ModulePresentation


class PerformanceComplete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    playback_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    reason: Literal['ended', 'error', 'expired'] = 'ended'


class WelcomeComplete(PerformanceComplete):
    welcome_id:str=Field(pattern=r'^[a-f0-9]{32}$')


@router.post('/{module_id}/resume-welcome', dependencies=[Depends(local_request)])
async def resume_welcome(module_id:str,data:WelcomeComplete,request:Request):
    local_request(request);state=request.app.state
    async with state.settings_lock:
        service=state.modules.services.get(module_id)
        if not service or not hasattr(service,'resume_welcome'):raise HTTPException(404)
        await service.resume_welcome(data.playback_id,data.welcome_id)
        return {'active_module':state.pipeline.stage.owner or 'chat'}


@router.post('/{module_id}/complete', dependencies=[Depends(local_request)])
async def complete(module_id: str, data: PerformanceComplete, request: Request):
    local_request(request)
    state = request.app.state
    async with state.settings_lock:
        if module_id not in state.modules.services:
            raise HTTPException(404)
        service = state.modules.service(module_id)
        if not hasattr(service, 'complete'):
            raise HTTPException(422, 'This module is not a finite performance')
        await service.complete(data.playback_id, reason=data.reason)
        return {'active_module': state.pipeline.stage.owner or 'chat'}


@router.get('', dependencies=[Depends(local_request)])
async def modules(request: Request):
    local_request(request)
    return request.app.state.modules.public()


@router.post('/{module_id}/presentation', dependencies=[Depends(local_request)])
async def presentation(module_id: str, data: PresentationUpdate, request: Request):
    local_request(request)
    state = request.app.state
    async with state.settings_lock:
        spec = state.modules.specs.get(module_id)
        if not spec:
            raise HTTPException(404, 'Unknown module')
        if data.profile_id != state.active_character or data.revision != state.characters.revision:
            raise HTTPException(409, '角色或设置已改变，请重新打开模块设置。')
        if state.pipeline.performance_active or state.pipeline._is_replying:
            raise HTTPException(409, '请等待回复结束或停止演唱后再保存。')
        # Old controllers do not know the expression controls. Preserve saved
        # values they omit instead of silently re-enabling a disabled profile.
        current = state.modules.presentation(module_id, data.profile_id)
        look = ModulePresentation.model_validate({**current, **data.presentation.model_dump(exclude_unset=True)})
        if look.pose not in spec.poses or look.expression not in spec.expressions:
            raise HTTPException(422, '这个模块不支持所选姿势或表情。')
        library = state.characters.model_copy(deep=True)
        library.profiles[data.profile_id].modules[module_id] = look
        library.revision += 1
        try:
            save_library(library, state.character_path)
        except OSError:
            raise HTTPException(500, '模块设置保存失败，现有设置未改变。') from None
        state.characters = library
        await state.pipeline._emit(type='module_settings_updated', modules=state.modules.public(),
                                   profile_context=library.context(state.active_character))
        return state.modules.public()
