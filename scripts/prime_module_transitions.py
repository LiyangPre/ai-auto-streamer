"""Cache the selected character's short module transitions without playing them."""
import asyncio
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api_settings import ConnectionStore, connection_path
from src.characters import library_path, load_library
from src.config import Config
from src.module_transitions import ModuleTransitions
from src import module_transitions
from src.pipeline import Pipeline
from src.studio import settings_path, load_saved
from src.tts import TTSEngine


async def main(timeout=30, wait_only=False):
    # Offline preparation can wait longer; live cold-cache admission keeps its
    # normal six-second ceiling. Replaying a cached line never calls TTS.
    module_transitions.PREPARE_TIMEOUT_SECONDS = timeout
    config = Config.load('config.yaml')
    studio_path = settings_path('config.yaml')
    load_saved(config, studio_path)
    characters = load_library(config, library_path(studio_path))
    profile_id = characters.default_id
    characters.profiles[profile_id].settings(characters.scene).apply(config)
    connections = ConnectionStore(connection_path(studio_path), config)
    connections.load()
    connections.apply(config)
    pipeline = Pipeline(config)
    pipeline.tts = TTSEngine(config.tts) if config.tts.engine != 'none' else None
    state = SimpleNamespace(active_character=profile_id, characters=characters)
    transitions = ModuleTransitions(pipeline, state)
    result = []
    try:
        if wait_only:
            clip = await transitions.prepare_wait()
            item = {'module':'chat','phase':'wait','ready':bool(clip)}
            if clip:item.update(text=clip['text'],audio_url=clip['audio_url'],duration=clip['duration'])
            result.append(item)
            print(json.dumps(item,ensure_ascii=False),flush=True)
        for module in (() if wait_only else ('singing', 'story')):
            for _ in range(3):
                pair = await transitions.prepare_pair(module)
                for phase, clip in pair.items():
                    item = {'module': module, 'phase': phase, 'ready': bool(clip)}
                    if clip:
                        item.update(text=clip['text'], audio_url=clip['audio_url'], duration=clip['duration'])
                    result.append(item)
                    print(json.dumps(item, ensure_ascii=False), flush=True)
    finally:
        await transitions.close()
        if pipeline.tts:
            await pipeline.tts.close()
    report = {'profile_id': profile_id, 'clips': result, 'ready': sum(item['ready'] for item in result),
              'total': len(result), 'playback_started': False}
    target = Path('data/qa/reply-wait-cache.json' if wait_only else 'data/qa/module-transition-cache.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, choices=range(6, 61), default=30)
    parser.add_argument('--wait-only', action='store_true', help='Prepare the delayed-reply line without playing it')
    args=parser.parse_args()
    asyncio.run(main(args.timeout, args.wait_only))
