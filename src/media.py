"""Local, shared media library. Filenames are labels, never storage paths."""
import json
import math
import re
import shutil
import uuid
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from mutagen import MutagenError
from mutagen.mp3 import MP3
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

LIMITS = {'background': 20 * 1024 * 1024, 'music': 50 * 1024 * 1024}
EXTENSIONS = {'background': '.png', 'music': '.mp3'}
MAX_ASSETS = 200
MAX_LIBRARY_BYTES = 1024 * 1024 * 1024


def media_directory(studio_path):
    path = Path(studio_path)
    name = path.stem.replace('studio-settings', 'media-library')
    return path.parent / (name if name != path.stem else name + '-media')


class MediaAsset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: Literal['background', 'music']
    name: str = Field(min_length=1, max_length=120)
    size: int = Field(gt=0)
    width: int | None = None
    height: int | None = None
    duration: float | None = None

    @property
    def filename(self):
        return self.id + EXTENSIONS[self.kind]

    def public(self):
        return {**self.model_dump(), 'url': '/media/' + self.filename}


class MediaStore:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, asset_id):
        if not re.fullmatch(r'[a-f0-9]{32}', asset_id or ''):
            raise ValueError('Unknown media')
        asset = MediaAsset.model_validate_json((self.root / (asset_id + '.json')).read_text(encoding='utf-8'))
        if asset.id != asset_id or not (self.root / asset.filename).is_file():
            raise ValueError('Missing media')
        return asset

    def catalog(self):
        result = []
        for path in sorted(self.root.glob('*.json')):
            try:
                result.append(self.get(path.stem))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda item: (item.kind, item.name.casefold(), item.id))

    def validate_selection(self, scene):
        if scene.background == 'custom' and not scene.background_asset:
            raise ValueError('请先上传并选择 PNG 背景。')
        for asset_id, kind in [(scene.background_asset, 'background'), (scene.music_asset, 'music')]:
            if asset_id is None:
                continue
            try:
                asset = self.get(asset_id)
                if asset.kind != kind:
                    raise ValueError('Wrong media type')
            except (OSError, ValueError):
                raise ValueError('所选素材不可用，请重新上传或选择内置素材。') from None

    def store(self, stream, size, kind, original_name):
        # Windows and Unix path separators are removed, even on another host.
        name = original_name.replace('\\', '/').rsplit('/', 1)[-1]
        name = ''.join(char for char in name if char.isprintable()).strip()
        if not name or len(name) > 120 or Path(name).suffix.lower() != EXTENSIONS[kind]:
            raise ValueError('请选择 .png 背景或 .mp3 音乐，文件名不超过 120 个字符。')
        existing = self.catalog()
        if len(existing) >= MAX_ASSETS or sum(item.size for item in existing) + size > MAX_LIBRARY_BYTES:
            raise ValueError('本地素材库已满（最多 200 个文件 / 1 GB）。')
        stream.seek(0)
        info = {}
        try:
            if kind == 'background':
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(stream) as picture:
                        width, height = picture.size
                        if (picture.format != 'PNG' or picture.is_animated or max(width, height) > 8192
                                or width * height > 25_000_000):
                            raise ValueError('背景需为静态 PNG，最长边不超过 8192 像素，总像素不超过 2500 万。')
                        picture.verify()
                    # verify checks PNG chunks; load also rejects damaged pixel data.
                    stream.seek(0)
                    with Image.open(stream) as picture:
                        picture.load()
                    info = {'width': width, 'height': height}
            else:
                audio = MP3(stream).info
                if audio.layer != 3 or audio.sketchy or not math.isfinite(audio.length) or audio.length <= 0:
                    raise ValueError('请上传有效的 MP3 音频。')
                info = {'duration': round(audio.length, 2)}
        except (UnidentifiedImageError, OSError, SyntaxError, MutagenError,
                Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise ValueError('文件内容无法读取，请上传有效的 PNG 图片或 MP3 音频。') from None
        asset = MediaAsset(id=uuid.uuid4().hex, kind=kind, name=name, size=size, **info)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / asset.filename
        metadata = self.root / (asset.id + '.json')
        temporary = self.root / (asset.id + '.tmp')
        try:
            stream.seek(0)
            with path.open('xb') as output:
                shutil.copyfileobj(stream, output, length=256 * 1024)
            temporary.write_text(json.dumps(asset.model_dump(), ensure_ascii=False), encoding='utf-8')
            temporary.replace(metadata)
        except BaseException:
            path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            raise
        return asset
