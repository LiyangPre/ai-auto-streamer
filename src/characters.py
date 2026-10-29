"""Character-owned settings and a shared stage, persisted in one atomic document.

The flat studio-settings file is a compatibility export. This library is the
source of truth after migration; credentials and platform connections stay in Config.
"""
import json
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .config import SceneConfig
from .studio import StrictModel, IdentitySettings, ReplySettings, VoiceSettings, MotionSettings, StudioSettings


class ModulePresentation(StrictModel):
    pose: str = Field(default='auto', pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    expression: str = Field(default='auto', pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    motion_strength: float = Field(default=.35, ge=0, le=1, allow_inf_nan=False)
    expression_enabled: bool = Field(default=True, strict=True)
    expression_strength: float = Field(default=.55, ge=0, le=1, allow_inf_nan=False, strict=True)
    scene_preset: Literal['inherit', 'moonlit', 'palace', 'garden', 'car', 'singing-room', 'transparent'] = 'inherit'


class CharacterProfile(StrictModel):
    identity: IdentitySettings
    reply: ReplySettings
    voice: VoiceSettings
    actions: MotionSettings
    modules: dict[str, ModulePresentation] = Field(default_factory=dict, max_length=32)
    module_options: dict[str, dict] = Field(default_factory=dict, max_length=32)
    # Explicit asset binding. Additional artwork packs need their own calibrated
    # pose/face assets; a different identity must not silently claim new artwork.
    artwork_id: Literal["astra"] = "astra"

    @model_validator(mode='after')
    def legacy_module_scene_defaults(self):
        # Existing singing looks predate the dedicated microphone scene. Resolve
        # that new default in memory; an explicit shared-stage choice is retained.
        singing = self.modules.get('singing')
        if singing is not None and 'scene_preset' not in singing.model_fields_set:
            singing.scene_preset = 'singing-room'
        return self

    @classmethod
    def from_settings(cls, settings):
        return cls.model_validate(settings.model_dump(exclude={"scene"}))

    def settings(self, scene):
        return StudioSettings(**self.model_dump(exclude={"artwork_id", "modules", "module_options"}), scene=scene.model_copy(deep=True))


class CharacterLibrary(StrictModel):
    version: Literal[1] = 1
    default_id: Literal["astra"] = "astra"
    revision: int = Field(default=0, ge=0)
    profiles: dict[str, CharacterProfile]
    scene: SceneConfig

    @model_validator(mode="after")
    def valid_profiles(self):
        if "astra" not in self.profiles or not 1 <= len(self.profiles) <= 24:
            raise ValueError("Astra and at most 24 character profiles are required")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", key) for key in self.profiles):
            raise ValueError("Invalid character identifier")
        return self

    def context(self, active_id):
        return {"active_id": active_id, "default_id": self.default_id, "revision": self.revision,
                "profiles": [{"id": key, "name": p.identity.name, "spoken_name": p.identity.spoken_name,
                              "voice_provider": p.voice.provider, "voice_enabled": p.voice.enabled,
                              "voice_name": {"mimo-tts": 'Voice Design' if p.voice.mimo_model=='mimo-v2.5-tts-voicedesign' else p.voice.mimo_voice, "glm-tts": p.voice.glm_voice,
                                             "minimax": p.voice.minimax_voice}.get(p.voice.provider, p.voice.voice),
                              "artwork_id": p.artwork_id} for key, p in self.profiles.items()],
                "artworks": [{"id": "astra", "name": "Astra · 银龙", "thumbnail": "/static/scenes/car/astra-car-v1.png"}]}


def library_path(studio_path):
    path = Path(studio_path)
    name = path.stem.replace("studio-settings", "character-profiles")
    if name == path.stem:
        name += "-profiles"
    return path.with_name(name + ".json")


def save_library(library, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(library.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_library(config, path):
    if path.exists():
        try:
            return CharacterLibrary.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Never overwrite unreadable profiles with a fresh one-profile seed.
            raise ValueError(f"Character library is unreadable: {path.name}. Restore its backup before starting.") from None
    settings = StudioSettings.from_config(config)
    library = CharacterLibrary(profiles={"astra": CharacterProfile.from_settings(settings)}, scene=settings.scene)
    save_library(library, path)
    return library


def duplicate_profile(library, source_id, name):
    profile = library.profiles[source_id].model_copy(deep=True)
    old = profile.identity
    persona = old.persona
    names = sorted({old.name, old.spoken_name} - {""}, key=len, reverse=True)
    if names:
        persona = re.sub("|".join(re.escape(word) for word in names), lambda _: name, persona)
    profile.identity = IdentitySettings(name=name, spoken_name=name, persona=persona)
    profile_id = "character-" + uuid.uuid4().hex[:12]
    library.profiles[profile_id] = profile
    return profile_id
