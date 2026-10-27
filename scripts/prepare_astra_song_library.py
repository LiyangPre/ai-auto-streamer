"""Prepare three original mini-songs with Astra's saved MiMo voice, then back up her vocals.

This makes paid/provider requests only for missing titles. It never broadcasts.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import Config, PROJECT_ROOT
from src.studio import settings_path, load_saved
from src.characters import library_path, load_library
from src.api_settings import ConnectionStore, connection_path
from src.singing import SingingStudio, SongDraft, songs_directory

ORIGINALS = (
    ('星星顺风车', '晚风坐上星星的车\n银龙哼着轻轻的歌\n路过云朵和一条小河\n今天的快乐分你一半吧', '轻快的流行小曲，旋律明亮，温柔清唱'),
    ('云朵口袋', '把小小云朵装进口袋\n带着好心情向你走来\n如果今天有一点疲惫\n就让这首小歌陪你发呆', '舒缓的轻爵士小曲，轻柔清唱，尾音自然'),
    ('晚安小银龙', '月亮悄悄关上了窗\n星星守着你的梦想\n小银龙把烦恼轻轻藏\n晚安愿你醒来都是晴朗', '安静的摇篮曲，柔和清唱，连贯而缓慢'),
)


async def main():
    cfg = Config.load()
    path = settings_path('config.yaml')
    load_saved(cfg, path)
    library = load_library(cfg, library_path(path))
    library.profiles['astra'].settings(library.scene).apply(cfg)
    connections = ConnectionStore(connection_path(path), cfg)
    connections.load(); connections.apply(cfg)
    if not cfg.tts.resolve_api_key('mimo-tts'):
        raise SystemExit('Configure MiMo in API connections first.')
    pipeline = SimpleNamespace(performance_active=False, performance_idle=asyncio.Event())
    studio = SingingStudio(songs_directory(path), pipeline)
    results = []
    for title, lyrics, direction in ORIGINALS:
        previous = next((song for song in studio.catalog() if song['profile_id'] == 'astra' and song['title'] == title), None)
        if not previous:
            draft = SongDraft(title=title, lyrics=lyrics,
                              style=cfg.tts.mimo_style + '\n' + direction + '。按顺序唱完这四行，不要添加歌词。')
            studio.prepare(draft, cfg.tts, 'astra')
            await studio.task
            if studio.job['state'] != 'ready':
                print(json.dumps({'title': title, 'job': studio.job}, ensure_ascii=False), flush=True)
                continue
            previous = next(song for song in studio.catalog() if song['id'] == studio.job['id'])
        result = {key: previous[key] for key in ('id', 'title', 'voice', 'duration', 'generation_ms')}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    backup = studio.backup('astra')
    proof = {'original_material': True, 'songs': results, 'backup': backup,
             'note': 'Pre-generated audio, awaiting human musical-quality audition; no exact-cover or pitch claim.'}
    destination = PROJECT_ROOT / 'data/qa/astra-song-library.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'backup': backup}, ensure_ascii=False), flush=True)
    if len(results) != len(ORIGINALS):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
