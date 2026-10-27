"""Prepare the ten original stories with Astra's saved voice-design description; no broadcast."""
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.config import Config,PROJECT_ROOT
from src.studio import load_saved,settings_path
from src.characters import load_library,library_path
from src.api_settings import ConnectionStore,connection_path
from src.story import StoryStudio,stories_directory

async def main():
    cfg=Config.load();path=settings_path('config.yaml');load_saved(cfg,path)
    library=load_library(cfg,library_path(path));library.profiles['astra'].settings(library.scene).apply(cfg)
    connections=ConnectionStore(connection_path(path),cfg);connections.load();connections.apply(cfg)
    studio=StoryStudio(stories_directory(path),SimpleNamespace());studio.prepare(cfg.tts,'astra')
    while not studio.task.done():
        await asyncio.wait({studio.task},timeout=20)
        print(json.dumps(studio.job,ensure_ascii=False),flush=True)
    await studio.task
    result=studio.public('astra',cfg.tts)
    proof={'job':studio.job,'model':result['model'],'clips':[entry['clip'] for entry in result['stories'] if entry['ready']]}
    destination=PROJECT_ROOT/'data/qa/astra-stories.json';destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(proof,ensure_ascii=False),flush=True)
    if studio.job['state']!='ready':raise SystemExit(1)
if __name__=='__main__':asyncio.run(main())
