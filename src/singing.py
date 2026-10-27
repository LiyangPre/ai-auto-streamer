"""Prepare bounded MiMo vocal performances; playback never calls the provider."""
import asyncio
import json
import time
import uuid
import hashlib
import re
import zipfile
import shutil
from pathlib import Path

from mutagen.mp3 import MP3
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .mimo_tts import mimo_audio_chunks
from .provider_network import http_client, failure
from .performance import PerformanceSession

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


class SingingStudio(PerformanceSession):
    def __init__(self, root, pipeline):
        super().__init__(pipeline, 'singing')
        self.root = Path(root)
        self.pipeline = pipeline
        self.task = None
        self.timer = None
        self.job = {'state': 'idle'}
        self.playback = None

    def module_status(self):
        return 'Singing' if self.playback else 'Preparing' if self.busy else 'Ready'

    def snapshot(self):
        return self.playback

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
        return {'job': self.job, 'songs': self.catalog(), 'playback': self.playback,
                'demo_lyrics': DEMO_LYRICS.split('\n\n')[0], 'max_seconds': MAX_SECONDS}

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
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.stop()
