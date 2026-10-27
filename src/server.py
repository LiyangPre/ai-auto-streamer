"""FastAPI web server with WebSocket support"""
import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import time
import re
import tempfile
from urllib.parse import unquote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import Config, PROJECT_ROOT
from .pipeline import Pipeline
from .danmaku import DanmakuMessage, stable_user_id
from .studio import StudioSettings, VoicePreview, VOICE_CATALOG, GLM_VOICES, MIMO_VOICES, MINIMAX_VOICES, settings_path, load_saved, save_settings
from .tts import TTSEngine
from .characters import CharacterProfile, library_path, load_library, save_library, duplicate_profile
from .studio import StrictModel
from .media import MediaStore, media_directory, LIMITS

logger = logging.getLogger(__name__)

pipeline: Pipeline = None
config: Config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, config
    config = Config.load(getattr(app.state, "config_path", "config.yaml"))
    app.state.studio_path = settings_path(getattr(app.state, "config_path", "config.yaml"))
    load_saved(config, app.state.studio_path)
    app.state.character_path = library_path(app.state.studio_path)
    app.state.characters = load_library(config, app.state.character_path)
    app.state.active_character = app.state.characters.default_id
    app.state.characters.profiles[app.state.active_character].settings(app.state.characters.scene).apply(config)
    app.state.settings_lock = asyncio.Lock()
    app.state.media = MediaStore(media_directory(app.state.studio_path))
    app.state.media_lock = asyncio.Lock()
    app.state.preview_tts = TTSEngine(config.tts.model_copy(deep=True))
    app.state.preview_lock = asyncio.Lock()
    pipeline = Pipeline(config)
    await pipeline.start()

    logger.info(f"Server started: http://{config.server.host}:{config.server.port}")
    try:
        yield
    finally:
        await pipeline.stop()
        await app.state.preview_tts.close()


app = FastAPI(lifespan=lifespan)

class DanmakuRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    user_name: str = Field(default="Web用户", min_length=1, max_length=40)
    user_id: int | None = Field(default=None, ge=1)
    msg_type: Literal["danmaku", "gift", "enter"] = "danmaku"
    gift_name: str | None = Field(default=None, max_length=60)
    gift_num: int = Field(default=0, ge=0, le=100000)
    price: float = Field(default=0, ge=0, allow_inf_nan=False)
    priority: int = Field(default=0, ge=0, le=3)


class TestEventRequest(BaseModel):
    type: Literal["idle_test", "action_test"]
    action: str = "idle"


class DirectorReply(BaseModel):
    text: str = Field(min_length=2, max_length=160)
    emotion: Literal["normal", "happy", "sad", "angry", "surprised", "thinking"] = "normal"


@app.get("/")
async def root():
    return FileResponse(PROJECT_ROOT / "frontend/index.html")


@app.get("/control")
async def control():
    return FileResponse(PROJECT_ROOT / "frontend/control.html")


@app.get("/rig-preview")
async def rig_preview():
    return FileResponse(PROJECT_ROOT / "frontend/rig-preview.html")


@app.get("/health")
async def health():
    healthy = pipeline.healthy if pipeline else False
    return {"status": "ok" if healthy else "degraded", "pipeline_running": healthy}


@app.get("/api/status")
async def get_status():
    return pipeline.status()


@app.get("/api/studio")
async def get_studio():
    return {"settings": StudioSettings.from_config(config).model_dump(), "voices": VOICE_CATALOG,
            "minimax_voices": MINIMAX_VOICES,
            "glm_voices": GLM_VOICES,
            "mimo_voices": MIMO_VOICES,
            "mimo_ready": TTSEngine.provider_ready(config.tts.model_copy(update={"engine": "mimo-tts"})),
            "glm_ready": TTSEngine.provider_ready(config.tts.model_copy(update={"engine": "glm-tts"})),
            "minimax_ready": TTSEngine.provider_ready(config.tts.model_copy(update={"engine": "minimax"})),
            "actions": pipeline.action_mapper.get_all_action_names(), "profile_context": profile_context()}


def profile_context():
    return app.state.characters.context(app.state.active_character)


@app.get('/api/media')
async def list_media():
    assets = await run_in_threadpool(app.state.media.catalog)
    return {'assets': [item.public() for item in assets], 'limits': LIMITS}


@app.post('/api/media/{kind}')
async def upload_media(kind: Literal['background', 'music'], request: Request,
                       filename: str = Header(alias='X-Filename', max_length=1500)):
    limit = LIMITS[kind]
    declared = request.headers.get('content-length')
    if declared and (not declared.isdigit() or int(declared) > limit):
        raise HTTPException(413, '文件过大：PNG 最多 20 MB，MP3 最多 50 MB。')
    # Raw file bodies avoid a second multipart copy. Spooling keeps RAM bounded;
    # parsing and disk work run off the event loop so live chat stays responsive.
    async with app.state.media_lock:
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, '文件过大：PNG 最多 20 MB，MP3 最多 50 MB。')
                await run_in_threadpool(stream.write, chunk)
            if not size:
                raise HTTPException(422, '文件为空。')
            try:
                asset = await run_in_threadpool(app.state.media.store, stream, size, kind, unquote(filename))
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            except OSError:
                raise HTTPException(500, '无法保存素材，请检查本地磁盘空间。') from None
    return {'asset': asset.public()}


@app.get('/media/{filename}')
async def media_file(filename: str):
    match = re.fullmatch(r'([a-f0-9]{32})\.(png|mp3)', filename)
    if not match:
        raise HTTPException(404)
    try:
        asset = app.state.media.get(match[1])
        if asset.filename != filename:
            raise ValueError('Wrong extension')
    except (ValueError, OSError):
        raise HTTPException(404) from None
    return FileResponse(app.state.media.root / filename,
                        media_type='image/png' if asset.kind == 'background' else 'audio/mpeg',
                        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'public, max-age=31536000, immutable'})


def check_revision(revision, profile_id=None):
    if revision is not None and revision != app.state.characters.revision:
        raise HTTPException(409, "设置已在其他窗口更新，请丢弃旧草稿并重新读取后再保存。")
    if profile_id is not None and profile_id != app.state.active_character:
        raise HTTPException(409, "当前角色已切换，旧草稿不会覆盖新角色。请重新读取。")


def check_voice(settings):
    candidate = config.tts.model_copy(deep=True)
    settings.voice.apply(candidate)
    if not TTSEngine.provider_ready(candidate):
        variable = {"mimo-tts": "MIMO_API_KEY", "glm-tts": "GLM_API_KEY", "minimax": "MINIMAX_API_KEY"}.get(candidate.engine, "API_KEY")
        raise HTTPException(422, f"请在项目 .env 中填写 {variable} 并重启，或先关闭该角色的语音。")


async def commit_character_settings(library, active_id, changed=False):
    settings = library.profiles[active_id].settings(library.scene)
    library.revision += 1
    # Persist the authoritative whole library before changing the running session.
    try:
        save_library(library, app.state.character_path)
    except OSError:
        raise HTTPException(500, "角色档案保存失败，当前角色未改变。") from None
    try:
        save_settings(settings, app.state.studio_path)
    except OSError:
        logger.warning("Could not refresh legacy studio-settings export; character library is saved")
    old_tts = pipeline.tts
    app.state.characters = library
    app.state.active_character = active_id
    settings.apply(config)
    pipeline.action_mapper.config = config.action
    if pipeline.llm:
        pipeline.llm.character = config.character
        if hasattr(pipeline.llm, "_build_system_prompt"):
            pipeline.llm.system_prompt = pipeline.llm._build_system_prompt()
    if changed:
        pipeline.memory.clear_character_replies()
        pipeline._warmup_bag.clear()
        pipeline._last_warmup_line = None
        pipeline._last_activity_time = time.time()
        # Cancel any progressive synthesis still attached to the previous voice.
        pipeline.tts = None
        if old_tts:
            await old_tts.close()
    if config.tts.engine == "none":
        if pipeline.tts:
            await pipeline.tts.close()
        pipeline.tts = None
    elif pipeline.tts is None:
        pipeline.tts = TTSEngine(config.tts)
    else:
        pipeline.tts.config = config.tts
    result = {"status": "ok", "settings": settings.model_dump(), "profile_context": profile_context(), "profile_changed": changed}
    await pipeline._emit(type="settings_updated", **result)
    return result


@app.post("/api/studio")
async def update_studio(settings: StudioSettings,
                        profile_id: str | None = Header(default=None, alias="X-Profile-ID"),
                        revision: int | None = Header(default=None, alias="X-Profile-Revision")):
    async with app.state.settings_lock:
        check_revision(revision, profile_id)
        check_voice(settings)
        try:
            app.state.media.validate_selection(settings.scene)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if pipeline._is_replying:
            raise HTTPException(409, "正在生成回复，请稍后保存；当前编辑内容会保留。")
        async with pipeline._reply_lock:
            library = app.state.characters.model_copy(deep=True)
            # Older controller clients omit warm-up lines. Freeze this profile's
            # current lines instead of inheriting whichever character runs next.
            if settings.actions.warmup_lines is None:
                settings.actions.warmup_lines = config.idle.self_talk_prompts.copy()
            library.profiles[app.state.active_character] = CharacterProfile.from_settings(settings)
            library.scene = settings.scene.model_copy(deep=True)
            return await commit_character_settings(library, app.state.active_character)


class CreateCharacter(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    source_id: str = Field(default="astra", max_length=48)
    revision: int = Field(ge=0)


class ActivateCharacter(StrictModel):
    profile_id: str = Field(min_length=1, max_length=48)
    revision: int = Field(ge=0)


@app.get("/api/characters")
async def get_characters():
    return profile_context()


@app.post("/api/characters")
async def create_character(data: CreateCharacter):
    async with app.state.settings_lock:
        check_revision(data.revision)
        library = app.state.characters.model_copy(deep=True)
        if data.source_id not in library.profiles:
            raise HTTPException(404, "找不到要复制的角色。")
        if len(library.profiles) >= 24:
            raise HTTPException(422, "最多保存 24 个角色。")
        if any(p.identity.name == data.name for p in library.profiles.values()):
            raise HTTPException(409, "已有同名角色，请换一个名字。")
        profile_id = duplicate_profile(library, data.source_id, data.name)
        library.revision += 1
        try:
            save_library(library, app.state.character_path)
        except OSError:
            raise HTTPException(500, "角色档案保存失败，请重试。") from None
        app.state.characters = library
        await pipeline._emit(type="profiles_updated", profile_context=profile_context())
        return {"id": profile_id, "profile_context": profile_context()}


@app.post("/api/characters/activate")
async def activate_character(data: ActivateCharacter):
    async with app.state.settings_lock:
        check_revision(data.revision)
        library = app.state.characters.model_copy(deep=True)
        if data.profile_id not in library.profiles:
            raise HTTPException(404, "找不到这个角色。")
        settings = library.profiles[data.profile_id].settings(library.scene)
        check_voice(settings)
        # Do not silently discard viewer replies or pending paid interactions.
        if pipeline._is_replying or any(not t.done() for t in pipeline._background_tasks):
            raise HTTPException(409, "正在准备回复，请稍后切换。")
        async with pipeline._reply_lock:
            return await commit_character_settings(library, data.profile_id, data.profile_id != app.state.active_character)


@app.post("/api/voice-preview")
async def preview_voice(data: VoicePreview):
    voice = data.voice
    preview_config = config.tts.model_copy(deep=True)
    voice.apply(preview_config)
    preview_config.engine = voice.provider  # Audition also works while live speech is muted.
    if not TTSEngine.provider_ready(preview_config):
        if preview_config.engine == "mimo-tts":
            raise HTTPException(422, "MiMo 尚未配置。在 .env 填写 MIMO_API_KEY 并重启后即可试听。")
        if preview_config.engine == "glm-tts":
            raise HTTPException(422, "GLM 尚未配置。在 .env 填写 GLM_API_KEY 并重启后即可试听。")
        raise HTTPException(422, "MiniMax 尚未配置。在 .env 填写 MINIMAX_API_KEY 并重启后即可试听。")
    async with app.state.preview_lock:
        app.state.preview_tts.config = preview_config
        url = await app.state.preview_tts.synthesize(data.text, "normal")
    if not url:
        raise HTTPException(502, "试听生成失败，请重试。")
    voice_id = {"glm-tts": voice.glm_voice, "mimo-tts": voice.mimo_voice, "minimax": voice.minimax_voice}.get(voice.provider, voice.voice)
    return {"audio_url": url, "voice": voice_id, "provider": voice.provider}


@app.post("/api/direct-reply")
async def direct_reply(data: DirectorReply):
    if not data.text.strip() or not pipeline.safety.allows_output(data.text):
        raise HTTPException(422, "该台词未通过内容检查。")
    if len(pipeline._background_tasks) >= 8:
        raise HTTPException(429, "待播台词较多，请稍后再试。")
    pipeline._spawn(pipeline._speak_direct(data.text.strip(), data.emotion, source="director"))
    return {"status": "queued"}


@app.get("/api/voice-stream/{filename}")
async def voice_stream(filename: str):
    if not re.fullmatch(r"tts_[0-9]+_[a-f0-9]{8}\.mp3", filename):
        raise HTTPException(404)
    engine = pipeline.tts
    if engine and filename in engine.jobs:
        return StreamingResponse(engine.stream_bytes(filename), media_type="audio/mpeg",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    path = PROJECT_ROOT / "data/audio" / filename
    if path.exists() and path.stat().st_size:
        return FileResponse(path, media_type="audio/mpeg")
    raise HTTPException(404)


@app.get("/api/config")
async def get_config():
    """Expose runtime config the frontend needs to pick a renderer."""
    live2d = config.live2d
    model_url = ""
    if live2d.model_path:
        # data/models/xxx/yyy.model3.json -> /models/xxx/yyy.model3.json
        normalized = live2d.model_path.replace("\\", "/")
        prefix = "data/models/"
        if normalized.startswith(prefix):
            model_url = "/models/" + normalized[len(prefix):]
        elif normalized.startswith("models/"):
            model_url = "/" + normalized
        else:
            model_url = normalized

    vrm_model_url = ""
    if config.vrm.model_path:
        normalized = config.vrm.model_path.replace("\\", "/")
        prefix = "data/models/"
        if normalized.startswith(prefix):
            vrm_model_url = "/models/" + normalized[len(prefix):]
        elif normalized.startswith("models/"):
            vrm_model_url = "/" + normalized
        else:
            vrm_model_url = normalized

    return {
        "profile_context": profile_context(),
        "renderer": live2d.renderer,
        "scene": config.scene.model_dump(),
        "model_url": model_url,
        "vrm_model_url": vrm_model_url,
        "scale": live2d.scale,
        "sprite_dir": live2d.sprite_dir,
        "rig_url": "/static/avatar_dragon/rig.json" if live2d.sprite_dir == "avatar_dragon" else "",
        "character": config.character.name,
        "provider": config.llm.provider,
        "api_key_required": config.llm.provider not in {"mock", "ollama"},
        "api_key_configured": config.llm.api_key_configured,
        "api_key_env": config.llm.api_key_env or f"{config.llm.provider.upper()}_API_KEY",
        "model": config.llm.model,
        "platform": config.platform.type,
        "room_id": config.platform.room_id,
        "tts_engine": config.tts.engine,
        "voice": {"glm-tts": config.tts.glm_voice, "mimo-tts": config.tts.mimo_voice, "minimax": config.tts.minimax_voice}.get(config.tts.engine, config.tts.voice),
        "filter_window": config.danmaku_filter.time_window,
        "min_length": config.danmaku_filter.min_length,
        "max_length": config.danmaku_filter.max_length,
        "cooldown": config.reply_interval.cooldown if config.reply_interval.enabled else 0,
        "template_routes": sum(r.enabled and r.use_template for r in config.router.routes.values())
            if config.router.enabled else 0,
        "sprite_mouth": {"x": 0.494, "y": 0.157, "width": 0.018, "height": 0.009}
            if live2d.sprite_dir == "avatar_dragon" else None,
        "actions": pipeline.action_mapper.get_all_action_names() if pipeline else [],
    }


@app.post("/api/danmaku")
async def send_danmaku(data: DanmakuRequest):
    content = data.content.strip()
    limits = config.danmaku_filter
    if not content or (data.msg_type == "danmaku" and not limits.min_length <= len(content) <= limits.max_length):
        raise HTTPException(422, f"弹幕长度应为 {limits.min_length}–{limits.max_length} 字")
    if not pipeline.safety.allows_input(content):
        raise HTTPException(422, "该内容未通过输入检查")
    fields = data.model_dump(exclude={"user_id", "content", "user_name"})
    user_name = data.user_name.strip() or "Web用户"
    msg = DanmakuMessage(content=content, user_name=user_name,
                         user_id=data.user_id or stable_user_id(user_name), timestamp=time.time(), **fields)
    if not pipeline.danmaku_queue.offer(msg):
        raise HTTPException(429, "队列已满，请稍后重试")
    return {"status": "ok"}


@app.get("/api/memory")
async def get_memory():
    return pipeline.memory.snapshot()


@app.post("/api/test-event")
async def test_event(data: TestEventRequest):
    event_type = data.type
    if event_type == "idle_test":
        await pipeline.trigger_idle_action()
    elif event_type == "action_test":
        if data.action not in pipeline.action_mapper.get_all_action_names():
            raise HTTPException(422, "未知动作")
        await pipeline.trigger_action(data.action)
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({**pipeline.status(), "settings": StudioSettings.from_config(config).model_dump(), "profile_context": profile_context()})
    pipeline.ws_clients.add(websocket)
    logger.info(f"WebSocket client connected. Total: {len(pipeline.ws_clients)}")

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        pipeline.ws_clients.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total: {len(pipeline.ws_clients)}")


frontend_static = PROJECT_ROOT / "frontend/static"
if frontend_static.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_static)), name="static")

data_models = PROJECT_ROOT / "data/models"
if data_models.exists():
    app.mount("/models", StaticFiles(directory=str(data_models)), name="models")

data_audio = PROJECT_ROOT / "data/audio"
data_audio.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(data_audio)), name="audio")
