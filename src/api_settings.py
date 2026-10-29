"""Private local API settings. Public views contain presence flags, never keys."""
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .provider_network import route
from .glm_models import DEFAULT_GLM_MODEL, DEFAULT_GLM_FALLBACKS, GLM_CHAT_MODELS, PRICING_URL, validate_model_ids, validate_glm_no_thinking

Provider = Literal['glm', 'openai', 'deepseek', 'qwen', 'ollama', 'mock']
NetworkMode = Literal['direct', 'auto', 'environment']
KeySlot = Literal['text', 'mimo-tts', 'glm-tts', 'minimax']


def validate_base_url(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)
            or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in {'localhost','127.0.0.1','::1'}))):
        raise ValueError('Use HTTPS, or HTTP on localhost, without credentials or query parameters')
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TextConnection(Strict):
    provider: Provider = 'glm'
    base_url: str = Field(default='https://open.bigmodel.cn/api/paas/v4', max_length=400)
    model: str = Field(default=DEFAULT_GLM_MODEL, min_length=1, max_length=100, pattern=r'^[\w./:-]+$')
    network_mode: NetworkMode = 'direct'
    # This studio deliberately keeps fast replies; no hidden provider-default reasoning.
    enable_thinking: Literal[False] = False
    fallback_models: list[str] = Field(default_factory=list, max_length=7)
    fallback_attempt_timeout: float = Field(default=3.0, ge=0.05, le=30, allow_inf_nan=False)
    fallback_total_timeout: float = Field(default=20.0, ge=0.05, le=60, allow_inf_nan=False)
    fallback_cooldown: float = Field(default=90.0, ge=1, le=3600, allow_inf_nan=False)
    delay_notice_enabled: bool = True
    delay_notice_after: float = Field(default=3.0, ge=0.05, le=30, allow_inf_nan=False)
    delay_notice_cooldown: float = Field(default=45.0, ge=1, le=3600, allow_inf_nan=False)
    _url = field_validator('base_url')(validate_base_url)
    _models = field_validator('fallback_models')(validate_model_ids)

    @model_validator(mode='after')
    def no_forced_reasoning(self):
        if self.provider == 'glm':
            for model in [self.model, *self.fallback_models]:
                validate_glm_no_thinking(model)
            self.model = self.model.lower()
        return self


class MimoConnection(Strict):
    base_url: str = Field(default='https://api.xiaomimimo.com/v1', max_length=400)
    network_mode: NetworkMode = 'auto'

    @field_validator('base_url')
    @classmethod
    def official_endpoint(cls, value):
        value = validate_base_url(value)
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname.endswith('.xiaomimimo.com')
                or parsed.port not in (None,443) or parsed.path.rstrip('/') != '/v1'
                or 'token-plan' in parsed.hostname or 'batch-' in parsed.hostname):
            raise ValueError('Use the official MiMo pay-as-you-go /v1 endpoint from your console')
        return value


class ConnectionUpdate(Strict):
    revision: int = Field(ge=0)
    text: TextConnection
    mimo: MimoConnection
    keys: dict[KeySlot, SecretStr] = Field(default_factory=dict)

    @field_validator('keys')
    @classmethod
    def valid_keys(cls, values):
        for value in values.values():
            key = value.get_secret_value()
            if len(key)>2048 or any(ord(c)<33 or ord(c)>126 for c in key):
                raise ValueError('Invalid API key format')
        return values


class ConnectionTest(ConnectionUpdate):
    target: KeySlot


def connection_path(studio_path):
    # .env.* is already excluded from version control at every directory level.
    return Path(studio_path).with_name('.env.api-' + Path(studio_path).stem + '.json')


class ConnectionStore:
    def __init__(self, path, config):
        self.path = Path(path)
        self.revision = 0
        self.keys = {}
        self.text = TextConnection(**{name:getattr(config.llm,name) for name in TextConnection.model_fields})
        self.mimo = MimoConnection(base_url=config.tts.mimo_base_url, network_mode=config.tts.mimo_network_mode)

    def load(self):
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 32768:
                raise ValueError('Oversized settings')
            data=json.loads(self.path.read_text(encoding='utf-8'))
            self.text=TextConnection.model_validate(data['text'])
            self.mimo=MimoConnection.model_validate(data['mimo'])
            self.revision=int(data['revision'])
            allowed={'mimo-tts','glm-tts','minimax'}|{'text:'+p for p in ('glm','openai','deepseek','qwen','ollama','mock')}
            if not isinstance(data['keys'],dict) or not set(data['keys'])<=allowed:
                raise ValueError('Invalid key slots')
            self.keys={k:SecretStr(v).get_secret_value() for k,v in data['keys'].items() if isinstance(v,str)}
        except (OSError, ValueError, KeyError, TypeError):
            # Do not serialize validation errors containing stored secret inputs.
            raise RuntimeError('Local API settings are unreadable; restore the private API settings file.') from None

    def candidate(self, update):
        candidate=object.__new__(ConnectionStore)
        candidate.path=self.path;candidate.text=update.text.model_copy(deep=True)
        candidate.mimo=update.mimo.model_copy(deep=True)
        candidate.revision=self.revision+1;candidate.keys=self.keys.copy()
        for slot,secret in update.keys.items():
            value=secret.get_secret_value().strip()
            if value:
                candidate.keys['text:'+candidate.text.provider if slot=='text' else slot]=value
        return candidate

    def apply(self, config):
        old_provider=config.llm.provider
        for key,value in self.text.model_dump().items():
            setattr(config.llm,key,value)
        if old_provider != config.llm.provider:
            config.llm.api_key='';config.llm.api_key_env=''
        key=self.keys.get('text:'+self.text.provider)
        if key is not None:
            config.llm.api_key=key
        config.tts.mimo_base_url=self.mimo.base_url
        config.tts.mimo_network_mode=self.mimo.network_mode
        config.tts._api_keys={k:v for k,v in self.keys.items() if k in {'mimo-tts','glm-tts','minimax'}}
        if 'glm-tts' not in config.tts._api_keys and self.keys.get('text:glm'):
            config.tts._api_keys['glm-tts']=self.keys['text:glm']

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_name(self.path.name+'.tmp')
        data={'revision':self.revision,'text':self.text.model_dump(),'mimo':self.mimo.model_dump(),'keys':self.keys}
        try:
            # Restrictive permissions on POSIX; Windows retains the user's folder ACL.
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def public(self, config):
        return {'revision':self.revision,'text':self.text.model_dump(),'mimo':self.mimo.model_dump(),
                'glm_catalog':GLM_CHAT_MODELS,'glm_pricing_url':PRICING_URL,'glm_prices_checked':'2026-10-28',
                'glm_default_order':[DEFAULT_GLM_MODEL,*DEFAULT_GLM_FALLBACKS],
                'configured':{'text':config.llm.api_key_configured,
                    **{p:bool(config.tts.resolve_api_key(p)) for p in ('mimo-tts','glm-tts','minimax')}},
                'active_tts':config.tts.engine,
                'routes':{'text':route(self.text.network_mode)[0],'mimo':route(self.mimo.network_mode)[0]}}
