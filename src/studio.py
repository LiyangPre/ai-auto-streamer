"""Editable, non-secret character controls. API credentials never enter this file."""
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import PROJECT_ROOT, SceneConfig
from .motion_catalog import DEMO_ACTIONS, LEGACY_ACTIONS, demo_action

VOICE_CATALOG = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 · 自然温暖女声", "description": "成年女声 · 普通话"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊 · 轻快年轻女声", "description": "年轻女声 · 普通话"},
    {"id": "zh-CN-YunxiNeural", "name": "云希 · 自然青年男声", "description": "青年男声 · 普通话"},
    {"id": "zh-CN-YunjianNeural", "name": "云健 · 沉稳男声", "description": "成年男声 · 普通话"},
]
GLM_VOICES = [
    {"id": "tongtong", "name": "彤彤 · GLM 默认音色"},
    {"id": "xiaochen", "name": "小陈 · GLM 系统音色"},
    {"id": "chuichui", "name": "锤锤 · GLM 系统音色"},
]
MINIMAX_VOICES = [
    {"id": "female-yujie", "name": "御姐 · 冷静成年女声"},
    {"id": "wumei_yujie", "name": "妩媚御姐 · 柔和成熟"},
    {"id": "female-chengshu", "name": "成熟女性 · 稳重"},
]
MIMO_VOICES = [
    {"id": "茉莉", "name": "茉莉 · 中文女声"},
    {"id": "冰糖", "name": "冰糖 · 中文女声"},
    {"id": "苏打", "name": "苏打 · 中文男声"},
    {"id": "白桦", "name": "白桦 · 中文男声"},
]
ACTION_NAMES = DEMO_ACTIONS
EMOTIONS = ("normal", "happy", "sad", "angry", "surprised", "thinking")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IdentitySettings(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    spoken_name: str = Field(default="", max_length=40)
    persona: str = Field(min_length=1, max_length=1600)


class ReplySettings(StrictModel):
    style: str = Field(min_length=1, max_length=800)
    max_chars: int = Field(ge=16, le=120)
    max_tokens: int = Field(ge=48, le=256)
    ask_back: bool
    read_chat: bool = False
    templates: bool
    # Match YAML timing bounds so importing a valid low-latency configuration
    # cannot fail just because its value is outside the controller's preset.
    collection_window: float = Field(gt=0, le=10, allow_inf_nan=False)
    cooldown: float = Field(ge=0, le=10)


class VoiceSettings(StrictModel):
    provider: Literal["edge-tts", "glm-tts", "minimax", "mimo-tts"] = "edge-tts"
    mimo_voice: str = "茉莉"
    mimo_model: Literal['mimo-v2.5-tts', 'mimo-v2.5-tts-voicedesign'] = 'mimo-v2.5-tts-voicedesign'
    mimo_style: str = Field(default="成年女性，声线偏低，清冷从容，略带气声。语速稍慢，停顿自然，表达亲近而克制，咬字清晰。", max_length=800)
    glm_voice: str = "tongtong"
    minimax_voice: str = "female-yujie"
    minimax_pitch: int = Field(default=0, ge=-4, le=4)
    enabled: bool = True
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: int = Field(default=0, ge=-20, le=20)
    pitch: int = Field(default=0, ge=-8, le=8)
    volume: int = Field(default=0, ge=-10, le=10)
    expressive: bool = False
    delivery: Literal["fast", "natural"] = "fast"

    @field_validator("mimo_voice")
    @classmethod
    def supported_mimo_voice(cls, value):
        if value not in {voice["id"] for voice in MIMO_VOICES}:
            raise ValueError("Choose a MiMo voice from the audition list")
        return value

    @field_validator("glm_voice")
    @classmethod
    def supported_glm_voice(cls, value):
        if value not in {voice["id"] for voice in GLM_VOICES}:
            raise ValueError("Choose a GLM voice from the audition list")
        return value

    @field_validator("minimax_voice")
    @classmethod
    def supported_minimax_voice(cls, value):
        if value not in {voice["id"] for voice in MINIMAX_VOICES}:
            raise ValueError("Choose a MiniMax voice from the audition list")
        return value

    def apply(self, tts):
        tts.selected_provider = self.provider
        tts.engine = self.provider if self.enabled else "none"
        tts.voice = self.voice
        tts.glm_voice = self.glm_voice
        tts.mimo_voice = self.mimo_voice
        tts.mimo_model = self.mimo_model
        tts.mimo_style = self.mimo_style
        tts.minimax_voice = self.minimax_voice
        tts.minimax_pitch = self.minimax_pitch
        tts.rate = f"{self.rate:+d}%"
        tts.pitch = f"{self.pitch:+d}Hz"
        tts.volume = f"{self.volume:+d}%"
        tts.expressive = self.expressive
        tts.delivery = self.delivery
        tts.progressive_audio = True
        tts.max_sentence_chars = 48 if self.delivery == "fast" else 96
        tts.min_phrase_chars = 18 if self.delivery == "fast" else 24

    @field_validator("voice")
    @classmethod
    def supported_voice(cls, value):
        if value not in {voice["id"] for voice in VOICE_CATALOG}:
            raise ValueError("Choose a voice from the audition list")
        return value


class MotionSettings(StrictModel):
    automatic: bool
    intensity: float = Field(ge=0, le=1.2)
    idle_enabled: bool
    warmup_enabled: bool = False
    warmup_delay: float = Field(default=45, ge=15, le=300, allow_inf_nan=False)
    warmup_interval: float = Field(default=90, ge=30, le=600, allow_inf_nan=False)
    warmup_lines: list[str] | None = Field(default=None, max_length=20)
    emotion_map: dict[str, str]

    @field_validator("warmup_lines")
    @classmethod
    def valid_warmup_lines(cls, value):
        if value is not None and any(not line.strip() or len(line) > 160 for line in value):
            raise ValueError("Each warm-up line must contain 1–160 characters")
        return [line.strip() for line in value] if value is not None else None

    @field_validator("emotion_map")
    @classmethod
    def known_actions(cls, value):
        if set(value) != set(EMOTIONS) or any(action not in (*ACTION_NAMES, *LEGACY_ACTIONS) for action in value.values()):
            raise ValueError("Provide one valid action for each emotion")
        return {emotion: demo_action(action) for emotion, action in value.items()}


class StudioSettings(StrictModel):
    identity: IdentitySettings
    reply: ReplySettings
    voice: VoiceSettings
    actions: MotionSettings
    scene: SceneConfig = Field(default_factory=SceneConfig)

    @classmethod
    def from_config(cls, config):
        return cls(
            identity={"name": config.character.name, "spoken_name": config.character.spoken_name, "persona": config.character.personality},
            reply={"style": config.character.reply_style, "max_chars": config.character.max_reply_chars,
                   "max_tokens": config.llm.max_tokens, "ask_back": config.character.ask_back, "read_chat": config.character.read_chat,
                   "templates": config.router.enabled and any(r.use_template for r in config.router.routes.values()), "collection_window": config.danmaku_filter.time_window,
                   "cooldown": config.reply_interval.cooldown if config.reply_interval.enabled else 0},
            voice={"enabled": config.tts.engine != "none", "voice": config.tts.voice,
                   "provider": config.tts.engine if config.tts.engine != "none" else config.tts.selected_provider,
                   "glm_voice": config.tts.glm_voice,
                   "mimo_voice": config.tts.mimo_voice, "mimo_style": config.tts.mimo_style, "mimo_model": config.tts.mimo_model,
                   "minimax_voice": config.tts.minimax_voice, "minimax_pitch": config.tts.minimax_pitch,
                   "rate": int(config.tts.rate.rstrip("%")), "pitch": int(config.tts.pitch.removesuffix("Hz")),
                   "volume": int(config.tts.volume.rstrip("%")), "expressive": config.tts.expressive,
                   "delivery": config.tts.delivery},
            actions={"automatic": config.action.automatic, "intensity": config.action.intensity,
                     "warmup_enabled":config.idle.self_talk,"warmup_delay":config.idle.self_talk_delay,
                     "warmup_interval":config.idle.self_talk_interval,
                     "warmup_lines":config.idle.self_talk_prompts,
                     "idle_enabled": config.idle.enabled, "emotion_map": config.action.emotion_map},
            scene=config.scene.model_copy(deep=True),
        )

    def apply(self, config):
        config.character.name = self.identity.name
        config.character.spoken_name = self.identity.spoken_name
        config.tts.pronunciations = {self.identity.name: self.identity.spoken_name} if self.identity.spoken_name else {}
        config.character.personality = self.identity.persona
        config.character.reply_style = self.reply.style
        config.character.max_reply_chars = self.reply.max_chars
        config.character.ask_back = self.reply.ask_back
        config.character.read_chat = self.reply.read_chat
        config.llm.max_tokens = self.reply.max_tokens
        for route in config.router.routes.values():
            if not route.use_template:
                route.max_tokens = self.reply.max_tokens
        for name in ("question", "default"):
            if name in config.router.routes:
                config.router.routes[name].instruction = "自然、简洁地回应，遵循角色的说话方式。"
        config.router.enabled = True
        for name in ("greeting", "farewell", "enter"):
            if name in config.router.routes:
                config.router.routes[name].use_template = self.reply.templates
        config.danmaku_filter.time_window = self.reply.collection_window
        config.reply_interval.cooldown = self.reply.cooldown
        config.reply_interval.enabled = self.reply.cooldown > 0
        self.voice.apply(config.tts)
        config.action.automatic = self.actions.automatic
        config.action.intensity = self.actions.intensity
        config.action.emotion_map = self.actions.emotion_map.copy()
        config.idle.enabled = self.actions.idle_enabled
        config.idle.self_talk = self.actions.warmup_enabled
        config.idle.self_talk_delay = self.actions.warmup_delay
        config.idle.self_talk_interval = self.actions.warmup_interval
        if self.actions.warmup_lines is not None:
            config.idle.self_talk_prompts = self.actions.warmup_lines.copy()
        config.scene = self.scene.model_copy(deep=True)


class VoicePreview(StrictModel):
    voice: VoiceSettings
    text: str = Field(default="晚上好，我是露娜。先放松一下，今天有什么想和我分享的吗？", min_length=2, max_length=120)


def settings_path(config_path="config.yaml"):
    suffix = "" if Path(config_path).name == "config.yaml" else "-" + Path(config_path).stem
    return PROJECT_ROOT / "data" / f"studio-settings{suffix}.json"


def load_saved(config, path):
    if path.exists():
        try:
            StudioSettings.model_validate_json(path.read_text(encoding="utf-8")).apply(config)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Saved studio settings are unreadable; using config.yaml defaults")


def save_settings(settings, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
