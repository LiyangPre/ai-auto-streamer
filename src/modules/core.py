"""One lifecycle, API and browser manifest contract for studio features."""
from dataclasses import dataclass, field
import re
from typing import Callable, Any
from ..characters import ModulePresentation


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    description: str
    icon: str
    factory: Callable[[Any], Any]
    router: Any = None
    controller: str = ''
    output: str = ''
    defaults: dict = field(default_factory=lambda: {'pose': 'auto', 'expression': 'auto', 'motion_strength': .35})
    poses: tuple = ('auto', 'idle', 'nod', 'think', 'shy', 'laugh', 'present')
    expressions: tuple = ('auto', 'neutral', 'happy', 'shy', 'thinking', 'surprised', 'singing')

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', self.id):
            raise ValueError('Invalid module ID')
        for asset in (self.controller, self.output):
            if asset and not re.fullmatch(r'/static/[a-z0-9/_-]+\.js', asset):
                raise ValueError('Modules must use installed local scripts')

    def manifest(self):
        return {key: getattr(self, key) for key in
                ('id', 'title', 'description', 'icon', 'controller', 'output', 'defaults', 'poses', 'expressions')}


class ModuleRegistry:
    def __init__(self, specs, state):
        self.specs = {spec.id: spec for spec in specs}
        if len(self.specs) != len(specs):
            raise ValueError('Duplicate module ID')
        self.state = state
        self.services = {}

    async def start(self):
        try:
            for spec in self.specs.values():
                service = spec.factory(self.state)
                self.services[spec.id] = service
                if hasattr(service, 'start'):
                    await service.start()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        # Performances release the stage before chat shuts down.
        errors = []
        for service in reversed(list(self.services.values())):
            try:
                await service.close()
            except Exception as error:
                errors.append(error)
        self.services.clear()
        if errors:
            raise errors[0]

    def service(self, module_id):
        return self.services[module_id]

    def presentation(self, module_id, profile_id=None):
        profile = self.state.characters.profiles[profile_id or self.state.active_character]
        saved = profile.modules.get(module_id)
        if saved:
            return saved.model_dump()
        service = self.services.get(module_id)
        defaults = service.default_presentation(profile) if hasattr(service, 'default_presentation') else self.specs[module_id].defaults
        return ModulePresentation.model_validate(defaults).model_dump()

    def public(self):
        return {'active_profile': self.state.active_character, 'revision': self.state.characters.revision,
                'modules': [{**spec.manifest(), 'presentation': self.presentation(spec.id),
                             'status': self.services[spec.id].module_status()}
                            for spec in self.specs.values()]}

    def snapshot(self):
        return {name: service.snapshot() for name, service in self.services.items()
                if hasattr(service, 'snapshot')}
