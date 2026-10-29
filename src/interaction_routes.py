"""Local-only character interaction policy, with optimistic revision guards."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .audience_intents import AudienceIntentPolicy
from .characters import save_library
from .local_security import local_request

router = APIRouter(prefix='/api/interactions')


class InteractionPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    profile_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$', max_length=48)
    revision: int = Field(ge=0)
    policy: AudienceIntentPolicy


def _service(state):
    service = getattr(state, 'interactions', None)
    if service is None:
        raise HTTPException(503, '互动服务尚未就绪，请稍后重试。')
    return service


@router.get('', dependencies=[Depends(local_request)])
async def get_interactions(request: Request):
    local_request(request)
    return _service(request.app.state).view()


@router.post('', dependencies=[Depends(local_request)])
async def set_interactions(data: InteractionPolicyUpdate, request: Request):
    local_request(request)
    state = request.app.state
    service = _service(state)
    async with state.settings_lock:
        if data.profile_id != state.active_character or data.revision != state.characters.revision:
            raise HTTPException(409, '角色设置已改变，请重新载入互动设置。')
        library = state.characters.model_copy(deep=True)
        library.profiles[data.profile_id].module_options['interaction'] = data.policy.model_dump()
        library.revision += 1
        try:
            save_library(library, state.character_path)
        except OSError:
            raise HTTPException(500, '无法保存互动设置，当前设置已保留。') from None
        state.characters = library
        service._sync()
        await state.pipeline._emit(type='module_settings_updated', modules=state.modules.public(),
                                   profile_context=library.context(state.active_character))
        await service.publish(force=True)
        return service.view()
