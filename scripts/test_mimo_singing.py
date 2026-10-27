"""Generate an original singing test without connecting to Bilibili or broadcasting."""
import argparse
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
from src.singing import SingingStudio, SongDraft, DEMO_LYRICS


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--long',action='store_true',help='Repeat the original verses to test a longer single request')
    parser.add_argument('--short',action='store_true',help='Test just the first original verse')
    args=parser.parse_args()
    config=Config.load();path=settings_path('config.yaml');load_saved(config,path)
    library=load_library(config,library_path(path))
    library.profiles[library.default_id].settings(library.scene).apply(config)
    connections=ConnectionStore(connection_path(path),config);connections.load();connections.apply(config)
    if not config.tts.resolve_api_key('mimo-tts'):
        raise SystemExit('MiMo key missing; configure it in the controller.')
    pipeline=SimpleNamespace(performance_active=False,performance_idle=asyncio.Event(),_last_activity_time=0)
    studio=SingingStudio(PROJECT_ROOT/'data/songs',pipeline)
    lyrics=DEMO_LYRICS+('\n\n'+DEMO_LYRICS if args.long else '')
    if args.short:lyrics=DEMO_LYRICS.split('\n\n')[0]
    draft=SongDraft(title='银龙晚风 · 原创长篇测试' if args.long else '银龙晚风 · 原创短段测试' if args.short else '银龙晚风 · 原创测试',lyrics=lyrics)
    studio.prepare(draft,config.tts,library.default_id)
    await studio.task
    result={'job':studio.job,'lyrics_characters':len(lyrics),'requested_song':'海屿你 · 马也_Crabbit / Cole先生 (2022)',
            'test_material':'Original lyrics, not the requested song',
            'pitch_accuracy_vs_original':'Not measured: reference audio and lyrics have not been supplied'}
    if studio.job['state']=='ready':
        song=next(s for s in studio.catalog() if s['id']==studio.job['id'])
        result['song']={k:v for k,v in song.items() if k!='lyrics'}
        result['local_audio']=str(studio.root/(song['id']+'.mp3'))
    destination=PROJECT_ROOT/('data/qa/mimo-singing-short-test.json' if args.short else 'data/qa/mimo-singing-test.json')
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if studio.job['state']!='ready':raise SystemExit(1)


if __name__=='__main__':asyncio.run(main())
