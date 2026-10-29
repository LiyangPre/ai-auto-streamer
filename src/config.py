"""Configuration management for AI Auto Streamer"""
from pathlib import Path
import os
from typing import Dict, Optional, Literal

import yaml
from pydantic import BaseModel, Field, ConfigDict, PrivateAttr, field_validator, model_validator
from dotenv import load_dotenv
from .glm_models import validate_model_ids, validate_glm_no_thinking

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class PlatformConfig(BaseModel):
    type: str = "text"
    room_id: int = 0
    room_url: str = ""
    sessdata_env: str = 'BILIBILI_SESSDATA'


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = Field(default="", exclude=True, repr=False)
    api_key_env: str = ""
    model: str = "qwen2.5:7b-instruct"
    temperature: float = 0.7
    max_tokens: int = 128
    stream: bool = True
    request_timeout: float = 45.0
    first_chunk_timeout: float = 15.0
    network_mode: Literal['auto', 'direct', 'environment'] = 'environment'
    fallback_models: list[str] = Field(default_factory=list, max_length=7)
    fallback_attempt_timeout: float = Field(default=3.0, ge=0.05, le=30, allow_inf_nan=False)
    fallback_total_timeout: float = Field(default=20.0, ge=0.05, le=60, allow_inf_nan=False)
    fallback_cooldown: float = Field(default=90.0, ge=1, le=3600, allow_inf_nan=False)
    delay_notice_enabled: bool = True
    delay_notice_after: float = Field(default=3.0, ge=0.05, le=30, allow_inf_nan=False)
    delay_notice_cooldown: float = Field(default=45.0, ge=1, le=3600, allow_inf_nan=False)
    _models = field_validator('fallback_models')(validate_model_ids)

    @model_validator(mode='after')
    def glm_fast_models(self):
        if self.provider == 'glm':
            models=[self.model, *self.fallback_models] if self.fallback_models or self.model.lower().startswith('glm-') else []
            for model in models:
                validate_glm_no_thinking(model)
            if self.model.lower().startswith('glm-'):
                self.model = self.model.lower()
            self.enable_thinking = False
        return self
    # 直播弹幕无复杂推理场景，默认关闭思考以降低首字延迟。
    # 非推理模型会忽略这两个参数（通过 extra_body 透传，服务端不识别即无效）。
    enable_thinking: bool = False
    # "" 表示不传；可选 low / medium / high（各家取值不同）
    reasoning_effort: str = ""

    def resolve_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "").strip()
        names = [f"{self.provider.upper()}_API_KEY"]
        if self.provider == "glm":
            names += ["ZAI_API_KEY", "ZHIPU_API_KEY"]
        return next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), "")

    @property
    def api_key_configured(self) -> bool:
        return bool(self.resolve_api_key())


class TTSConfig(BaseModel):
    _api_keys: dict[str, str] = PrivateAttr(default_factory=dict)
    engine: str = "edge-tts"
    selected_provider: str = "edge-tts"
    glm_voice: str = "tongtong"
    glm_api_key_env: str = "GLM_API_KEY"
    mimo_api_key_env: str = "MIMO_API_KEY"
    mimo_base_url: str = 'https://api.xiaomimimo.com/v1'
    mimo_network_mode: Literal['auto', 'direct', 'environment'] = 'auto'
    mimo_model: Literal['mimo-v2.5-tts', 'mimo-v2.5-tts-voicedesign'] = 'mimo-v2.5-tts-voicedesign'
    mimo_voice: str = "茉莉"
    mimo_style: str = Field(default="成年女性，声线偏低，清冷从容，略带气声。语速稍慢，停顿自然，表达亲近而克制，咬字清晰。", max_length=800)
    minimax_voice: str = "female-yujie"
    minimax_model: str = "speech-2.8-turbo"
    minimax_pitch: int = Field(default=0, ge=-4, le=4)
    minimax_api_key_env: str = "MINIMAX_API_KEY"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    timeout: float = Field(default=20.0, gt=0)
    sentence_stream: bool = True
    max_sentence_chars: int = Field(default=48, ge=8, le=200)
    min_phrase_chars: int = Field(default=12, ge=4, le=40)
    delivery: str = "fast"
    progressive_audio: bool = True
    expressive: bool = False
    pronunciations: dict[str, str] = {"Lunaria": "露娜"}
    # 情绪 → 音色/韵律覆盖。voice 省略则沿用默认 voice。
    # edge-tts 不支持 SSML express-as，用 voice/rate/pitch/volume 叠加情绪色彩。
    emotion_voice: dict = {
        "happy": {"rate": "+10%", "pitch": "+8Hz", "volume": "+10%"},
        "sad": {"rate": "-15%", "pitch": "-6Hz", "volume": "-10%"},
        "angry": {"rate": "+15%", "pitch": "+4Hz", "volume": "+15%"},
        "surprised": {"rate": "+12%", "pitch": "+10Hz", "volume": "+10%"},
        "thinking": {"rate": "-10%", "pitch": "+0Hz", "volume": "+0%"},
        "normal": {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
    }

    def resolve_api_key(self, provider=None):
        provider = provider or self.engine
        if provider in self._api_keys:
            return self._api_keys[provider]
        name = {'glm-tts':self.glm_api_key_env, 'mimo-tts':self.mimo_api_key_env, 'minimax':self.minimax_api_key_env}.get(provider)
        return os.environ.get(name, '').strip() if name else ''


class ActionConfig(BaseModel):
    mode: str = "emotion_map"
    automatic: bool = True
    intensity: float = Field(default=0.6, ge=0, le=1.2)
    emotion_map: dict[str, str] = {"normal": "idle", "happy": "laugh", "sad": "think", "angry": "think", "surprised": "nod", "thinking": "think"}


class DanmakuFilterConfig(BaseModel):
    enabled: bool = True
    # 时间窗口（秒），在这个时间窗口内的弹幕会被收集
    time_window: float = Field(default=0.6, gt=0, le=10)
    # 最大队列大小
    max_queue_size: int = Field(default=10, ge=1, le=1000)
    ingress_max_size: int = Field(default=100, ge=1, le=10000)
    # 最小弹幕长度
    min_length: int = 2
    # 最大弹幕长度
    max_length: int = 50
    # 窗口内来自不同观众的多条弹幕，聚合为一次回复并点名多人（提升互动密度）
    batch_max: int = Field(default=3, ge=1, le=20)


class ReplyIntervalConfig(BaseModel):
    # 回复间隔（秒）
    cooldown: float = 3.0
    # 是否启用回复间隔
    enabled: bool = True
    max_wait: float = 15.0
    paid_max_wait: float = 20.0


class SafetyConfig(BaseModel):
    enabled: bool = True
    blocked_keywords: list = ["自杀", "恐怖主义", "制造炸弹", "儿童色情"]
    provider: Literal['local', 'external'] = 'local'
    require_remote_for_bilibili: bool = False
    timeout: float = Field(default=3, gt=0, le=15)
    blocked_categories: list[Literal['abuse', 'sexual', 'advertising', 'danger', 'sensitive_topics']] = [
        'abuse', 'sexual', 'advertising', 'danger', 'sensitive_topics']


class IdleConfig(BaseModel):
    # 空闲阈值（秒），超过此时间无弹幕则触发空闲行为
    threshold: float = 30.0
    # 是否启用空闲行为
    enabled: bool = True
    # 空闲动作列表
    actions: list = ["normal", "thinking", "happy"]
    # 空闲自言自语（可选）
    self_talk: bool = False
    self_talk_delay: float = Field(default=45, ge=15, le=300, allow_inf_nan=False)
    self_talk_interval: float = Field(default=90, ge=30, le=600, allow_inf_nan=False)
    self_talk_prompts: list[str] = [
        "刚才想装得高冷一点，结果尾巴先替我打了招呼。",
        "我把最软的一朵云留在这里，累了就靠一会儿。",
        "尾巴先收好，免得一开心，把旁边的小伙伴扫下台。",
        "今天的好运先放在这里，路过的时候，记得顺手带走。",
        "别看我站得很端正，心里的小龙已经开始晃尾巴了。",
        "我有在认真营业，只是偶尔，会被自己的尾巴分心。",
        "如果安静也算一种陪伴，那我现在做得还不错。",
        "今天的星星，好像比我的小心思还多。",
    ]


class Live2DConfig(BaseModel):
    # 渲染器: "auto" | "vrm" | "live2d" | "sprite"
    # auto: 优先尝试已配置的 VRM/Live2D 模型，失败则回退 sprite
    renderer: str = "auto"
    # Live2D 模型路径，相对项目根目录，如 "data/models/hiyori/hiyori.model3.json"
    model_path: str = ""
    scale: float = 0.3
    # sprite 渲染器的表情贴图目录（相对 frontend/static）
    sprite_dir: str = "avatar"


class VRMConfig(BaseModel):
    # VRM 模型路径，相对项目根目录，如 "data/models/character.vrm"
    model_path: str = ""


class DisplayConfig(BaseModel):
    width: int = 1440
    height: int = 1080
    background: str = "transparent"


class SceneConfig(BaseModel):
    """Portable scene framing, shared by controller previews and full output."""
    model_config = ConfigDict(extra="forbid")
    background: Literal["moonlit", "palace", "garden", "car", "singing-room", "transparent", "custom"] = "moonlit"
    background_asset: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    music_asset: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    effect: Literal["rain", "clouds", "sparkles", "none"] = "clouds"
    weather_amount: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    dim: float = Field(default=0.10, ge=0, le=0.65, allow_inf_nan=False)
    companions: bool = True
    car_motion: bool = True
    car_vibration: bool = True
    bottom_blur: float = Field(default=4, ge=0, le=12, allow_inf_nan=False)
    character_x: float = Field(default=0, ge=-45, le=45, allow_inf_nan=False)
    character_y: float = Field(default=0, ge=-35, le=35, allow_inf_nan=False)
    character_scale: float = Field(default=1, ge=0.4, le=2, allow_inf_nan=False)
    subtitle_seconds: float = Field(default=4, ge=1, le=20, allow_inf_nan=False)
    music_enabled: bool = True
    music_source: Literal['file', 'system'] = 'file'
    music_volume: float = Field(default=0.18, ge=0, le=0.4, allow_inf_nan=False)


class CharacterConfig(BaseModel):
    name: str = "小助手"
    spoken_name: str = ""
    personality: str = "你是一个活泼可爱的虚拟主播助手，用简短、口语化的方式回复观众。"
    reply_style: str = "自然、温暖的日常聊天，不刻意卖萌，不堆叠语气词，不复述观众的问题。"
    max_reply_chars: int = Field(default=48, ge=16, le=120)
    ask_back: bool = False
    read_chat: bool = False


class MemoryConfig(BaseModel):
    """短期记忆：全局滚动窗口 + per-user 记忆（主键为 user_id）"""
    enabled: bool = True
    # 记忆保留时长（秒），超时淘汰
    ttl_seconds: float = 600.0
    # 全局滚动窗口条数（用于感知直播间氛围、接梗）
    global_max: int = 12
    # 单个观众最多保留条数
    per_user_max: int = 6
    # 同一观众两次感谢的最小间隔（秒），防止礼物刷屏
    gift_thanks_cooldown: float = 20.0
    # 礼物连击合并窗口（秒），窗口内同一观众同一礼物累加数量后只感谢一次
    gift_combo_window: float = 4.0
    # 进场欢迎冷却（秒），同一观众不重复欢迎
    enter_welcome_cooldown: float = 300.0


class RouteConfig(BaseModel):
    """单条路由的生成参数覆盖"""
    # 是否优先用模板直出（0 次 LLM 调用）
    use_template: bool = False
    templates: list = []
    # 模板直出时固定使用的情绪
    emotion: str = "happy"
    # 未命中模板时走 LLM，用下面两个参数覆盖全局默认
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # 是否启用该路由（关闭后事件被静默丢弃）
    enabled: bool = True
    # 该路由额外的 system 指令（如感谢话术风格）
    instruction: str = ""


class RouterConfig(BaseModel):
    """意图路由：规则层命中封闭意图，未命中走 default。

    因直播间无复杂推理场景，所有路由统一低 max_tokens + 关思考。
    """
    enabled: bool = True
    # 意图关键词命中后，仍要求弹幕长度不超过此值才走模板直出
    template_max_length: int = 12
    routes: Dict[str, RouteConfig] = {
        "greeting": RouteConfig(
            use_template=True,
            emotion="happy",
            temperature=0.95,
            max_tokens=48,
            templates=[
                "你好你好！欢迎来到直播间～",
                "哈喽呀！看到你啦～",
                "嘿嘿，你好呀！",
            ],
        ),
        "farewell": RouteConfig(
            use_template=True,
            emotion="sad",
            temperature=0.95,
            max_tokens=48,
            templates=[
                "诶，要走了吗...下次再来呀！",
                "拜拜～路上小心哦！",
                "记得回来看我呀！",
            ],
        ),
        "persona": RouteConfig(
            emotion="happy",
            temperature=0.8,
            max_tokens=96,
            instruction="这是观众在问你自身的设定，用第一人称、符合人设地简短回答，不要说自己是AI或语言模型。",
        ),
        "gift": RouteConfig(
            emotion="surprised",
            temperature=0.95,
            max_tokens=64,
            instruction="这是观众送礼，要真诚地道谢，念出对方昵称和礼物名，语气惊喜，可以稍微夸张。",
        ),
        "enter": RouteConfig(
            enabled=False,
            use_template=True,
            emotion="happy",
            temperature=0.95,
            max_tokens=32,
            templates=[
                "欢迎{user}来到直播间～",
                "哇，{user}来啦！",
            ],
        ),
        "praise": RouteConfig(
            emotion="happy",
            temperature=0.95,
            max_tokens=64,
            instruction="观众在夸你，要害羞又开心地回应。",
        ),
        "question": RouteConfig(
            emotion="thinking",
            temperature=0.7,
            max_tokens=128,
            instruction="观众在提问，简短口语化地回答，答完后反问一句引导互动。",
        ),
        "default": RouteConfig(
            emotion="normal",
            temperature=0.8,
            max_tokens=128,
            instruction="回复末尾可以自然地抛出一个反问，引导观众继续聊。",
        ),
    }


class WelcomeConfig(BaseModel):
    enabled:bool=True
    window:float=Field(default=5,ge=.1,le=30)
    min_interval:float=Field(default=15,ge=1,le=120)
    max_names:int=Field(default=3,ge=1,le=8)
    repeat_cooldown:float=Field(default=300,ge=10,le=3600)


class Config(BaseModel):
    welcome:WelcomeConfig=Field(default_factory=WelcomeConfig)
    server: ServerConfig = ServerConfig()
    platform: PlatformConfig = PlatformConfig()
    llm: LLMConfig = LLMConfig()
    tts: TTSConfig = TTSConfig()
    action: ActionConfig = ActionConfig()
    danmaku_filter: DanmakuFilterConfig = DanmakuFilterConfig()
    reply_interval: ReplyIntervalConfig = ReplyIntervalConfig()
    safety: SafetyConfig = SafetyConfig()
    idle: IdleConfig = IdleConfig()
    live2d: Live2DConfig = Live2DConfig()
    vrm: VRMConfig = VRMConfig()
    display: DisplayConfig = DisplayConfig()
    scene: SceneConfig = Field(default_factory=SceneConfig)
    character: CharacterConfig = CharacterConfig()
    memory: MemoryConfig = MemoryConfig()
    router: RouterConfig = RouterConfig()

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        config_path = Path(path)
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**data)
        return cls()

    def save(self, path: str = "config.yaml"):
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, allow_unicode=True, default_flow_style=False)
