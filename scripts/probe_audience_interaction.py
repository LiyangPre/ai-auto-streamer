"""Small opt-in provider check. No TTS, playback, or live chat side effects."""
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api_settings import ConnectionStore, connection_path
from src.audience_interaction import CHAT_CONTRACT, parse_interaction
from src.characters import library_path, load_library
from src.config import Config
from src.llm import CallParams, LLMProcessor
from src.studio import load_saved, settings_path


async def main():
    config = Config.load(ROOT / 'config.yaml')
    path = settings_path(ROOT / 'config.yaml')
    load_saved(config, path)
    library = load_library(config, library_path(path))
    library.profiles[library.default_id].settings(library.scene).apply(config)
    connections = ConnectionStore(connection_path(path), config)
    connections.load()
    connections.apply(config)
    engine = LLMProcessor(config.llm, config.character)
    sample = [
        '好想听你唱歌呀，随便来一首？',
        '讲讲你跟Claude和DeepSeek家里好玩的事呗',
        '别放《小幸运》，现在不想听歌',
        '今天的衣服真好看',
    ]
    started = time.monotonic()
    result = {'provider': config.llm.provider, 'samples': sample}
    raw = None
    try:
        raw, _ = await asyncio.wait_for(engine.chat(
            json.dumps({'messages': [{'index': i, 'text': text, 'name': f'测试观众{i+1}'}
                                     for i, text in enumerate(sample)]}, ensure_ascii=False),
            CallParams(structured_json=True, structured_message_count=len(sample), max_tokens=800, temperature=.2,
                       instruction=CHAT_CONTRACT + ' reply 最多 80 个字。',
                       context=json.dumps({'studio_state': {'awaiting_song': False,
                                                           'available': ['小幸运', '起风了'], 'paused': []}}, ensure_ascii=False))), 40)
        reply = parse_interaction(raw, len(sample), 80)
        decisions = {d.index: d.intent if d.positive and d.current_request and d.confidence >= .8 else 'none'
                     for d in reply.intents}
        result.update(ok=True, model=engine.last_model, reply=reply.model_dump(),
                      meaning_check=all(decisions.get(i, 'none') == expected
                                        for i, expected in enumerate(['singing', 'story', 'none', 'none'])))
    except Exception as error:
        result.update(ok=False, error=type(error).__name__)
        if raw is not None:
            # Only the synthetic test response; never provider headers or keys.
            result['test_response'] = raw[:4000]
    finally:
        await engine.close()
    result['elapsed_seconds'] = round(time.monotonic()-started, 2)
    out = ROOT / 'data/qa/audience-interaction-provider.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
