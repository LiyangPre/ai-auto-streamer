"""Prepare bounded MiMo vocal performances; playback never calls the provider."""
import asyncio
import json
import time
import uuid
import hashlib
import re
import zipfile
import shutil
import math
from pathlib import Path

from mutagen.mp3 import MP3
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from .mimo_tts import mimo_audio_chunks
from .provider_network import http_client, failure
from .performance import PerformanceSession
from .song_requests import SongRequestQueue
from .lyrics import parse_lrc, save_song_metadata, MAX_LYRIC_OFFSET

MAX_SECONDS = 300
MAX_SONGS_PER_CHARACTER = 64
MAX_LIBRARY_BYTES = 1024 * 1024 * 1024


def songs_directory(studio_path):
    path = Path(studio_path)
    name = path.stem.replace('studio-settings', 'songs')
    return path.with_name(name if name != path.stem else name + '-songs')
DEMO_LYRICS = '''晚风把星星吹到窗边
小小的银龙眨眨双眼
把今天的烦恼放在一边
陪你慢慢走过这条街

啦啦啦 把月光装进口袋
啦啦啦 好心情跟着你来
路灯像糖果一颗颗亮起来
下一站还有温柔在等待

云朵给天空写了一封信
说你已经做得很用心
就算偶尔有一点点安静
也有一首小歌唱给你听

啦啦啦 把月光装进口袋
啦啦啦 好心情跟着你来
让微笑陪着星光轻轻摇摆
明天的故事我们慢慢猜'''


class SongDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='银龙晚风 · 原创测试', min_length=1, max_length=80)
    lyrics: str = Field(min_length=2, max_length=1800)
    style: str = Field(default='温柔、轻盈的成年女声，轻快的流行旋律，清唱。保持自然呼吸，完整唱完全部歌词。', max_length=600)


class SongPlay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    song_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    music: Literal['quiet', 'shared', 'accompaniment'] = 'quiet'
    backing_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    backing_volume: float = Field(default=.20, ge=0, le=.6, allow_inf_nan=False)
    vocal_volume: float = Field(default=.85, ge=0, le=1, allow_inf_nan=False)
    vocal_delay: float = Field(default=0, ge=-10, le=30, allow_inf_nan=False)
    bpm: int = Field(default=80, ge=40, le=180)


class SongPlaybackRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_seconds: float = Field(default=0, ge=0, le=600, strict=True, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, gt=0, le=601, strict=True, allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError('结束时间必须晚于开始时间。')
        return self

    def bounds(self, duration):
        end = duration if self.end_seconds is None else self.end_seconds
        if self.start_seconds >= duration or end > duration or end-self.start_seconds < .25:
            raise ValueError('演唱区间须在原曲时长内，且至少 0.25 秒。')
        return self.start_seconds, end


class SingingStudio(PerformanceSession):
    def __init__(self, root, pipeline, state=None):
        super().__init__(pipeline, 'singing')
        self.root = Path(root)
        self.pipeline = pipeline
        self.task = None
        self.timer = None
        self.job = {'state': 'idle'}
        self.playback = None
        self.state = state
        self.requests = SongRequestQueue(self, state) if state else None
        self.request_task = None
        self._request_profile = None
        self._initial_performance_end = getattr(pipeline, '_last_performance_end_time', 0.)

    async def start(self):
        if self.requests:
            self.pipeline.ingress_handlers.append(self.requests.on_message)
            self.request_task = asyncio.create_task(self.pipeline._supervise('song-requests', self._request_loop))

    async def _request_loop(self):
        while True:
            if self._request_profile != self.state.active_character:
                self._request_profile = self.state.active_character
                self.requests.changed.set()
            if self.requests.changed.is_set():
                self.requests.changed.clear()
                await self.pipeline._emit(type='song_requests_update', requests=self.requests.public())
            await self._advance_requests()
            await asyncio.sleep(.5)

    def _requests_can_start(self):
        p = self.pipeline
        if self.requests:
            self.requests.prune_blocked()
        if (not self.requests or not self.requests.policy().auto_play or not self.requests.public()['queue']
                or not p.output_clients or p.performance_active or p._is_replying
                or (getattr(p, 'interactions', None) and p.interactions.blocks_automatic_song)
                or any(not task.done() for task in p._background_tasks)
                or p.paid.items or p._paid_batch or p.welcomes.pending):
            return False
        if p.tts and any(not job.done for job in p.tts.jobs.values()):
            return False
        last_end = p._last_performance_end_time
        if last_end > self._initial_performance_end and time.time() - last_end < self.requests.policy().intermission_seconds:
            return False
        return not p.speech_pending()

    async def _advance_requests(self):
        if not self._requests_can_start():
            return False
        try:
            return await self.activate(from_requests=True)
        except ValueError:
            # The stage may have changed between the readiness check and lock.
            # An admitted request remains queued when playback cannot start.
            return False

    async def activate(self, data=None, *, from_requests=False):
        """One admission path for operator playback and queued saved songs."""
        if not self.state:
            raise ValueError('Studio character context is unavailable.')
        state, p = self.state, self.pipeline
        profile_id = state.active_character
        if from_requests and not self._requests_can_start():
            return False
        if not from_requests:
            selected = next((song for song in self.catalog()
                             if song['id'] == data.song_id and song['profile_id'] == profile_id), None)
            if not selected:
                raise ValueError('找不到当前角色已保存的歌曲。')
            if self.requests and self.requests.is_blocked(selected):
                raise ValueError('这首歌已被主播暂停，暂时不能播放。')
        transition_audio = await self.prepare_transition_audio()
        async with state.settings_lock:
            if state.active_character != profile_id:
                raise ValueError('角色已改变，请重新选择歌曲。')
            if from_requests and not self._requests_can_start():
                return False
            if p.stage.owner not in (None, 'singing'):
                raise ValueError('另一个模块正在使用舞台，请先停止它。')
            if p._is_replying or any(not task.done() for task in p._background_tasks):
                raise ValueError('正在准备回复，请稍后播放。')
            if p.tts and any(not job.done for job in p.tts.jobs.values()):
                raise ValueError('正在合成语音，请稍后播放。')
            entry = self.requests.pop_next() if from_requests else None
            if from_requests and not entry:
                return False
            try:
                if from_requests:
                    if entry['profile_id'] != state.active_character:
                        raise ValueError('点歌角色已改变，请重新选择。')
                    data = SongPlay(song_id=entry['song_id'])
                song = next((item for item in self.catalog()
                             if item['id'] == data.song_id and item['profile_id'] == state.active_character), None)
                if not song:
                    raise ValueError('找不到当前角色已保存的歌曲。')
                if self.requests and self.requests.is_blocked(song):
                    raise ValueError('这首歌已被主播暂停，暂时不能播放。')
                payload = data.model_dump()
                payload.update(presentation=state.modules.presentation('singing'), backing_url=None)
                transitions = getattr(p, 'transitions', None)
                payload.update({name: clip for name, clip in transition_audio.items()
                                if not transitions or transitions.matches_current(clip)})
                if entry:
                    payload['requested_by'] = entry['requester']
                if data.music == 'accompaniment':
                    try:
                        asset = state.media.get(data.backing_id)
                        if asset.kind != 'music' or asset.duration > 330 or asset.size > 20 * 1024 * 1024:
                            raise ValueError()
                        payload['backing_url'] = asset.public()['url']
                    except (OSError, ValueError):
                        raise ValueError('请选择不超过 330 秒、20 MB 的 MP3 伴奏。') from None
                async with p._reply_lock:
                    await self.play(payload, song)
                if self.requests:
                    if entry:
                        self.requests.mark_played(entry)
                    else:
                        self.requests.mark_song_played(data.song_id)
                return True
            except BaseException:
                if entry and from_requests:
                    self.requests.requeue_front(entry)
                raise

    def module_status(self):
        return 'Singing' if self.playback else 'Preparing' if self.busy else 'Ready'

    def snapshot(self):
        return self.playback

    async def play(self, data, clip):
        start, end = SongPlaybackRange.model_validate(clip.get('playback_range') or {}).bounds(clip['duration'])
        await super().play({**data, 'start_offset':start, 'play_duration':end-start}, clip)

    def set_playback_range(self, song_id, selected, profile_id):
        item = self._lyrics_song(song_id, profile_id)
        selected.bounds(item['duration'])
        item['playback_range'] = selected.model_dump()
        save_song_metadata(self.root/(song_id+'.json'), item)
        return item

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def catalog(self):
        songs = []
        for path in self.root.glob('*.json'):
            try:
                item = json.loads(path.read_text(encoding='utf-8'))
                if item['id'] == path.stem and path.with_suffix('.mp3').is_file():
                    songs.append(item)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(songs, key=lambda song: song['created'], reverse=True)

    def public(self):
        result = {'job': self.job, 'songs': self.catalog(), 'playback': self.playback,
                'demo_lyrics': DEMO_LYRICS.split('\n\n')[0], 'max_seconds': MAX_SECONDS}
        if self.requests:
            result['songs'] = [{**song, 'blocked': self.requests.is_blocked(song)} for song in result['songs']]
            result['requests'] = self.requests.public()
        return result

    def _lyrics_song(self, song_id, profile_id):
        if not isinstance(song_id, str) or not re.fullmatch(r'[a-f0-9]{32}', song_id):
            raise ValueError('请选择当前角色已保存的歌曲。')
        item = next((song for song in self.catalog()
                     if song['id'] == song_id and song['profile_id'] == profile_id), None)
        if item is None:
            raise ValueError('请选择当前角色已保存的歌曲。')
        return dict(item)

    def attach_lyrics(self, song_id, text, profile_id):
        item = self._lyrics_song(song_id, profile_id)
        parsed = parse_lrc(text)
        try:
            texts = [cue['text'] for cue in parsed['cues'] if cue['text']]
            texts.extend(parsed['metadata'].values())
            if not all(self.pipeline.safety.allows_output(value) for value in texts):
                raise ValueError('歌词未通过本地内容检查。')
        except Exception:
            raise ValueError('歌词未通过本地内容检查。') from None
        item.update(lyrics_cues=parsed['cues'], lyrics_offset=parsed['offset_seconds'],
                    lyrics_metadata=parsed['metadata'], lyrics_updated_at=time.time())
        save_song_metadata(self.root / (song_id + '.json'), item)
        return item

    def set_lyrics_offset(self, song_id, offset_seconds, profile_id):
        item = self._lyrics_song(song_id, profile_id)
        if not item.get('lyrics_cues'):
            raise ValueError('请先为这首歌上传 LRC 歌词。')
        if (type(offset_seconds) not in (int, float) or not math.isfinite(offset_seconds)
                or abs(offset_seconds) > MAX_LYRIC_OFFSET):
            raise ValueError('歌词偏移必须在正负 30 秒内。')
        item.update(lyrics_offset=float(offset_seconds), lyrics_updated_at=time.time())
        save_song_metadata(self.root / (song_id + '.json'), item)
        return item

    def remove_lyrics(self, song_id, profile_id):
        item = self._lyrics_song(song_id, profile_id)
        for field in ('lyrics_cues', 'lyrics_offset', 'lyrics_metadata', 'lyrics_updated_at'):
            item.pop(field, None)
        save_song_metadata(self.root / (song_id + '.json'), item)
        return item

    def ensure_capacity(self,profile_id,extra_bytes=0):
        songs=self.catalog()
        if sum(song['profile_id']==profile_id for song in songs)>=MAX_SONGS_PER_CHARACTER or len(songs)>=256:
            raise ValueError('本角色歌库已满（64 首），请先备份并整理旧曲目。')
        if sum(song.get('bytes',0)+song.get('guide_bytes',0) for song in songs)+extra_bytes>MAX_LIBRARY_BYTES:
            raise ValueError('本地歌库已达到 1 GB，请先备份并整理。')

    def import_audio(self,asset,media_root,profile_id):
        if asset.kind!='music' or asset.size>20*1024*1024 or asset.duration>600:
            raise ValueError('请选择 20 MB / 10 分钟以内的 MP3。')
        self.ensure_capacity(profile_id,asset.size)
        token=uuid.uuid4().hex;self.root.mkdir(parents=True,exist_ok=True)
        target=self.root/(token+'.mp3');temp=self.root/(token+'.part');meta=self.root/(token+'.json.tmp')
        item={'id':token,'title':Path(asset.name).stem[:80],'profile_id':profile_id,'duration':asset.duration,'bytes':asset.size,
              'source':'upload','voice':'Uploaded recording','model':'recorded-audio','created':time.time(),'generation_ms':0,
              'lyrics':'','url':f'/api/singing/audio/{token}'}
        try:
            shutil.copyfile(Path(media_root)/asset.filename,temp)
            meta.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(target);meta.replace(self.root/(token+'.json'))
        finally:
            temp.unlink(missing_ok=True);meta.unlink(missing_ok=True)
            if not (self.root/(token+'.json')).exists():target.unlink(missing_ok=True)
        return item

    def attach_guide(self,song_id,asset,media_root,profile_id):
        item=next((song for song in self.catalog() if song['id']==song_id and song['profile_id']==profile_id),None)
        if not item:raise ValueError('请先选择当前角色的曲目。')
        if asset.kind!='music' or asset.size>20*1024*1024 or abs(asset.duration-item['duration'])>2:
            raise ValueError('人声轨需小于 20 MB，与歌曲从同一位置开始，时长差不超过 2 秒。')
        if sum(song.get('bytes',0)+song.get('guide_bytes',0) for song in self.catalog())+asset.size-item.get('guide_bytes',0)>MAX_LIBRARY_BYTES:
            raise ValueError('本地歌库已达到 1 GB。')
        path=self.root/(song_id+'-guide.mp3');temp=path.with_suffix('.part')
        try:
            shutil.copyfile(Path(media_root)/asset.filename,temp);temp.replace(path)
            item['guide_url']=f'/api/singing/audio/{song_id}/guide'
            item['guide_bytes']=asset.size
            meta=self.root/(song_id+'.json.tmp');meta.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8');meta.replace(self.root/(song_id+'.json'))
        finally:temp.unlink(missing_ok=True)
        return item

    def backup(self, profile_id):
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', profile_id):
            raise ValueError('Invalid character')
        songs = [song for song in self.catalog() if song['profile_id'] == profile_id]
        if not songs:
            raise ValueError('当前角色还没有歌曲。')
        root = self.root.with_name(self.root.name + '-backups') / profile_id
        root.mkdir(parents=True, exist_ok=True)
        if len(list(root.glob('*.zip'))) >= 8:
            raise ValueError('已有 8 个歌曲备份，请先转移不再需要的备份。')
        token = uuid.uuid4().hex
        target = root / (token + '.zip')
        temporary = root / (token + '.part')
        manifest = {'version': 1, 'profile_id': profile_id, 'created': time.time(), 'songs': songs, 'sha256': {}}
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                for song in songs:
                    for suffix in ('.json', '.mp3'):
                        path = self.root / (song['id'] + suffix)
                        content = path.read_bytes()
                        name = 'songs/' + path.name
                        manifest['sha256'][name] = hashlib.sha256(content).hexdigest()
                        archive.writestr(name, content)
                    if song.get('guide_url'):
                        path=self.root/(song['id']+'-guide.mp3');content=path.read_bytes();name='songs/'+path.name
                        manifest['sha256'][name]=hashlib.sha256(content).hexdigest();archive.writestr(name,content)
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr('RESTORE.txt', 'Stop Studio. Copy the files inside songs/ into data/songs/ (or the matching alternate-config song directory). Keep existing files. Restart Studio and select the matching character. This archive contains vocals and lyrics only, not API keys, character profiles or backing tracks.\n')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {'id': token, 'profile_id': profile_id, 'count': len(songs), 'bytes': target.stat().st_size,
                'filename': profile_id + '-songs-' + time.strftime('%Y%m%d-%H%M%S') + '.zip',
                'url': f'/api/singing/backups/{profile_id}/{token}'}

    def prepare(self, draft, config, profile_id):
        if self.busy:
            raise ValueError('正在生成另一首歌，请等待或取消。')
        self.ensure_capacity(profile_id,8*1024*1024)
        token = uuid.uuid4().hex
        self.job = {'id': token, 'state': 'preparing', 'title': draft.title, 'started': time.time()}
        cfg = config.model_copy(deep=True)
        cfg.mimo_model = 'mimo-v2.5-tts'  # The design endpoint explicitly excludes singing.
        cfg.mimo_style = draft.style
        cfg.timeout = 240
        self.task = asyncio.create_task(self._generate(token, draft, cfg, profile_id))
        return self.job

    async def _generate(self, token, draft, config, profile_id):
        temporary = self.root / (token + '.part')
        target = self.root / (token + '.mp3')
        started = time.monotonic()
        first = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            async def generate():
                nonlocal first
                total = 0
                async with http_client(config.mimo_network_mode, config.timeout) as client:
                    with temporary.open('wb') as output:
                        async for chunk in mimo_audio_chunks(client, '(唱歌)' + draft.lyrics.strip(), config,
                                config.resolve_api_key('mimo-tts'), max_pcm_bytes=48000 * MAX_SECONDS):
                            if first is None:
                                first = round((time.monotonic() - started) * 1000)
                            total += len(chunk)
                            if total > 8 * 1024 * 1024:
                                raise ValueError('Song buffer exceeded')
                            output.write(chunk)
                return total
            size = await asyncio.wait_for(generate(), timeout=240)
            duration = MP3(temporary).info.length
            if not 0 < duration <= MAX_SECONDS + 1:
                raise ValueError('Invalid song duration')
            item = {'id': token, 'title': draft.title, 'duration': round(duration, 3),
                    'bytes': size, 'created': time.time(), 'profile_id': profile_id,
                    'voice': config.mimo_voice, 'lyrics': draft.lyrics, 'style': draft.style,
                    'first_audio_ms': first, 'generation_ms': round((time.monotonic() - started) * 1000),
                    'url': f'/api/singing/audio/{token}'}
            target_tmp = self.root / (token + '.json.tmp')
            target_tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
            target_tmp.replace(self.root / (token + '.json'))
            self.job = {'id': token, 'state': 'ready', 'title': draft.title}
        except asyncio.CancelledError:
            self.job = {'id': token, 'state': 'cancelled'}
            raise
        except Exception as error:
            self.job = {'id': token, 'state': 'error', **failure(error)}
        finally:
            temporary.unlink(missing_ok=True)
            (self.root / (token + '.json.tmp')).unlink(missing_ok=True)
            if not (self.root / (token + '.json')).exists():
                target.unlink(missing_ok=True)

    async def close(self):
        if self.requests and self.requests.on_message in self.pipeline.ingress_handlers:
            self.pipeline.ingress_handlers.remove(self.requests.on_message)
        if self.request_task and not self.request_task.done():
            self.request_task.cancel()
            await asyncio.gather(self.request_task, return_exceptions=True)
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.stop()
