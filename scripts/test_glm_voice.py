"""Bounded paid GLM audition using GLM_API_KEY from the local .env."""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from src.config import PROJECT_ROOT, TTSConfig
from src.tts import TTSEngine


async def main():
    load_dotenv(PROJECT_ROOT / '.env')
    destination = PROJECT_ROOT / 'data/qa'
    destination.mkdir(parents=True, exist_ok=True)
    text = '今天有点累？那就坐近一点，慢慢说。我在听。'
    results = []
    for voice in sys.argv[1:] or ['tongtong']:
        if voice not in {'tongtong', 'xiaochen', 'chuichui'}:
            raise SystemExit('Choose tongtong, xiaochen or chuichui')
        engine = TTSEngine(TTSConfig(engine='glm-tts', glm_voice=voice, rate='-4%'), destination)
        started = time.perf_counter()
        timing = []
        try:
            url = await engine.create_stream(text, on_first_audio=lambda: timing.append(round((time.perf_counter() - started) * 1000)))
            job = engine.jobs[url.rsplit('/', 1)[-1]]
            await job.task
            result = {'voice': voice, 'characters': len(text), 'ok': not job.error,
                      'first_mp3_ms': timing[0] if timing else None,
                      'complete_ms': round((time.perf_counter() - started) * 1000)}
            if not job.error:
                target = destination / f'glm-{voice}-sample.mp3'
                job.path.replace(target)
                result.update(audio=str(target), bytes=target.stat().st_size)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))
        finally:
            await engine.close()
    (destination / 'glm-auditions.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    if not all(result['ok'] for result in results):
        raise SystemExit(1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
