"""Local controller actions for generation, rehearsal and stage playback."""
import asyncio
import json
import math
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .api_routes import local_request
from .singing import SongDraft, SongPlay
from .config import PROJECT_ROOT
from pydantic import BaseModel,Field,ConfigDict

router = APIRouter(prefix='/api/singing')
PREPARATION_ROOT = PROJECT_ROOT / 'data/songs-converted'
MAX_PREPARED_TRACKS = 64


def read_bounded_json(path, limit):
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Preparation metadata is oversized.')
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError('Preparation metadata must be an object.')
    return data


def batch_ids(tracks):
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= MAX_PREPARED_TRACKS:
        raise ValueError('Active batch must contain 1–64 tracks.')
    identities = []
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('Invalid active batch track.')
        identity = track.get('bvid') or track.get('id')
        if (not isinstance(identity, str) or not re.fullmatch(r'BV[A-Za-z0-9]{10}', identity)
                or track.get('id', identity) != identity):
            raise ValueError('Invalid active batch track identity.')
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise ValueError('Active batch contains duplicate tracks.')
    return set(identities)


def preparation_status():
    result = {'state': 'idle', 'stage': 'waiting', 'ready': 0, 'total': 0,
              'message': 'No offline song preparation is running.'}
    try:
        result['total'] = len(batch_ids(read_bounded_json(PREPARATION_ROOT / 'jobs.json', 256 * 1024).get('tracks')))
    except (OSError, ValueError):
        pass
    try:
        raw = read_bounded_json(PREPARATION_ROOT / 'pipeline-state.json', 16384)
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        return {**result, 'state': 'unknown', 'message': 'Preparation status is temporarily unavailable.'}
    result['message'] = 'Offline song preparation progress.'
    for key in ('state', 'stage'):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', value):
            result[key] = value
    for key in ('current_title', 'message'):
        value = raw.get(key)
        if isinstance(value, str):
            # Never turn worker traces, local paths or credential messages into UI text.
            private = re.search(r'(?i)(?:[a-z]:[\\/]|\\\\|/(?:users|home|tmp|var|data|workspace)/|bearer\s|api[_ -]?key|sk-[a-z0-9]{8})', value)
            if not private:
                result[key] = ''.join(char for char in value if char.isprintable())[:240 if key == 'message' else 80]
    for key in ('ready', 'total'):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= MAX_PREPARED_TRACKS:
            result[key] = value
    for key in ('started', 'updated', 'estimated_seconds_remaining'):
        value = raw.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            result[key] = value
    if type(raw.get('pid')) is int and 0 < raw['pid'] < 2 ** 31:
        result['pid'] = raw['pid']
    return result


class PreparedImport(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.get('/preparation')
async def get_preparation(request: Request):
    local_request(request)
    return await asyncio.to_thread(preparation_status)


@router.post('/import-prepared')
async def import_prepared_batch(data: PreparedImport, request: Request):
    local_request(request)
    state = request.app.state
    async with state.settings_lock:
        if 'astra' not in state.characters.profiles:
            raise HTTPException(404, 'Astra character profile is missing.')
        studio = state.modules.service('singing')
        if studio.busy:
            raise HTTPException(409, 'Wait for the current vocal preparation before importing the library.')
        manifest_path = PREPARATION_ROOT / 'prepared.json'
        try:
            manifest = await asyncio.to_thread(read_bounded_json, manifest_path, 256 * 1024)
            jobs = await asyncio.to_thread(read_bounded_json, PREPARATION_ROOT / 'jobs.json', 256 * 1024)
            active_ids = batch_ids(jobs.get('tracks'))
        except FileNotFoundError:
            raise HTTPException(409, 'All songs in the active batch must finish before importing.') from None
        except (OSError, ValueError):
            raise HTTPException(422, 'Prepared song manifest is unavailable or invalid.') from None
        tracks = manifest.get('tracks')
        total = len(active_ids)
        if (manifest.get('ready') != total or manifest.get('total') != total
                or not isinstance(tracks, list) or len(tracks) != total
                or any(not isinstance(track, dict) or track.get('ready') is not True for track in tracks)):
            raise HTTPException(409, 'All songs in the active batch must finish before importing.')
        expected_steps = jobs.get('diffusion_steps')
        try:
            if (batch_ids(tracks) != active_ids or manifest.get('reference_sha256') != jobs.get('reference_sha256')
                    or manifest.get('voice_candidate') != jobs.get('voice_candidate')):
                raise ValueError('Prepared files belong to another batch.')
            if expected_steps is not None and (type(expected_steps) is not int or not 4 <= expected_steps <= 100
                    or any(track.get('conversion', {}).get('diffusion_steps') != expected_steps for track in tracks)):
                raise ValueError('Prepared files use a different conversion quality.')
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(409, {'code': 'batch_changed',
                'message': 'Prepared songs do not match the active batch or requested conversion quality.'}) from None
        # Check the current in-memory profile under the same lock used for its
        # edits. A final export and a voice change must never race publication.
        try:
            from scripts.import_prepared_songs import validate_reference
            reference_metadata = manifest.get('reference_metadata')
            if not isinstance(reference_metadata, str) or not reference_metadata:
                raise ValueError('Missing voice reference metadata')
            metadata_path = Path(reference_metadata)
            if not metadata_path.is_absolute():
                metadata_path = PROJECT_ROOT / metadata_path
            reference = await asyncio.to_thread(validate_reference, manifest, metadata_path, 'astra')
            current = state.characters.profiles['astra'].voice
            if (reference.get('profile_id') != 'astra' or current.provider != 'mimo-tts'
                    or reference.get('style') != current.mimo_style
                    or reference.get('model') != current.mimo_model):
                raise ValueError('Saved voice and prepared reference disagree')
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, {'code': 'voice_changed',
                'message': "Prepared songs no longer match Astra's saved voice/reference. Restart preparation with the current voice."}) from None
        try:
            from scripts.import_prepared_songs import import_prepared
            result = await asyncio.to_thread(import_prepared, manifest_path,
                studio.root, profile_id='astra', expected_total=total, expected_bvids=active_ids,
                expected_reference_hash=jobs['reference_sha256'], expected_steps=expected_steps)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(422, 'Prepared audio/reference validation or library backup failed; completed takes are preserved.') from None
        # The library can be refreshed in the current profile without switching or playing.
        if result.get('backup'):
            result['backup'] = {key: value for key, value in result['backup'].items() if key != 'path'}
        return result


class SongImport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    asset_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    song_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')


@router.post('/import')
async def import_song(data:SongImport,request:Request):
    local_request(request);state=request.app.state
    async with state.settings_lock:
        try:
            asset=state.media.get(data.asset_id);studio=state.modules.service('singing')
            if data.song_id:return await asyncio.to_thread(studio.attach_guide,data.song_id,asset,state.media.root,state.active_character)
            return await asyncio.to_thread(studio.import_audio,asset,state.media.root,state.active_character)
        except (ValueError,OSError):raise HTTPException(422,'请选择有效 MP3；整曲最多 20 MB / 10 分钟，人声口型轨需与整曲对齐且时长相近。') from None


@router.get('')
async def get_singing(request: Request):
    local_request(request)
    state = request.app.state
    result = state.modules.service('singing').public()
    result['songs'] = [song for song in result['songs'] if song['profile_id'] == state.active_character]
    result['profile_id'] = state.active_character
    result['profile_name'] = state.characters.profiles[state.active_character].identity.name
    result['preparation'] = await asyncio.to_thread(preparation_status)
    return result


@router.post('/prepare', status_code=202)
async def prepare(data: SongDraft, request: Request):
    local_request(request)
    state = request.app.state
    if not state.runtime_config.tts.resolve_api_key('mimo-tts'):
        raise HTTPException(422, '请先在 API connections 中保存 MiMo 密钥。')
    if not data.lyrics.strip() or not state.pipeline.safety.allows_output(data.lyrics):
        raise HTTPException(422, '请填写可播出的歌词。')
    try:
        return state.modules.service('singing').prepare(data, state.runtime_config.tts, state.active_character)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.post('/cancel')
async def cancel(request: Request):
    local_request(request)
    studio = request.app.state.modules.service('singing')
    if studio.busy:
        studio.task.cancel()
        await asyncio.gather(studio.task, return_exceptions=True)
    return studio.public()


@router.post('/play')
async def play(data: SongPlay, request: Request):
    local_request(request)
    state = request.app.state
    async with state.settings_lock:
        studio = state.modules.service('singing')
        song = next((item for item in studio.catalog() if item['id'] == data.song_id), None)
        if not song:
            raise HTTPException(404, '找不到已生成的歌曲。')
        if song['profile_id'] != state.active_character:
            raise HTTPException(409, '请切换到生成这首歌的角色后再播放。')
        pipeline = state.pipeline
        if pipeline.stage.owner not in (None, 'singing'):
            raise HTTPException(409, '另一个模块正在使用舞台，请先停止它。')
        if pipeline._is_replying or any(not t.done() for t in pipeline._background_tasks):
            raise HTTPException(409, '正在准备回复，请稍后播放。')
        if pipeline.tts and any(not job.done for job in pipeline.tts.jobs.values()):
            raise HTTPException(409, '正在合成语音，请稍后播放。')
        payload = data.model_dump()
        payload['presentation'] = state.modules.presentation('singing')
        payload['backing_url'] = None
        if data.music == 'accompaniment':
            try:
                asset = state.media.get(data.backing_id)
                if asset.kind != 'music':
                    raise ValueError()
                if asset.duration > 330 or asset.size > 20 * 1024 * 1024:
                    raise ValueError()
                payload['backing_url'] = asset.public()['url']
            except (OSError, ValueError):
                raise HTTPException(422, '请选择不超过 330 秒、20 MB 的 MP3 伴奏。') from None
        async with pipeline._reply_lock:
            await studio.play(payload, song)
        return await get_singing(request)


@router.post('/stop')
async def stop(request: Request):
    local_request(request)
    async with request.app.state.settings_lock:
        await request.app.state.modules.service('singing').stop()
    return await get_singing(request)


@router.get('/audio/{song_id}')
async def song_audio(song_id: str, request: Request):
    if not re.fullmatch(r'[a-f0-9]{32}', song_id):
        raise HTTPException(404)
    path = request.app.state.modules.service('singing').root / (song_id + '.mp3')
    if not path.is_file() or not path.with_suffix('.json').is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='audio/mpeg')


@router.get('/audio/{song_id}/guide')
async def guide_audio(song_id:str,request:Request):
    if not re.fullmatch(r'[a-f0-9]{32}',song_id):raise HTTPException(404)
    path=request.app.state.modules.service('singing').root/(song_id+'-guide.mp3')
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='audio/mpeg')


@router.post('/backup')
async def backup(request: Request):
    local_request(request)
    state = request.app.state
    # Keep the selected character stable while the archive is assembled off-loop.
    async with state.settings_lock:
        try:
            return await asyncio.to_thread(state.modules.service('singing').backup, state.active_character)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except OSError:
            raise HTTPException(500, '歌曲备份写入失败，原歌曲保留。') from None


@router.get('/backups/{profile_id}/{backup_id}')
async def download_backup(profile_id: str, backup_id: str, request: Request):
    local_request(request)
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', profile_id) or not re.fullmatch(r'[a-f0-9]{32}', backup_id):
        raise HTTPException(404)
    root = request.app.state.modules.service('singing').root
    path = root.with_name(root.name + '-backups') / profile_id / (backup_id + '.zip')
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='application/zip', filename=profile_id + '-songs.zip')
