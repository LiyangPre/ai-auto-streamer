"""Measure real first text and first audio bytes through the local controller API."""
import argparse
import asyncio
import json
import time
import statistics
from pathlib import Path
import aiohttp


PROMPTS = (
    '今天下班有点累，请用一句自然的中文安慰我，不要反问。',
    '我准备去散步，用一句自然的中文祝我玩得开心。',
    '我的绿萝长出新叶子了，用一句话回应这个小惊喜。',
)


async def measure(base_url, label, prompt):
    result = {'label': label, 'prompt': prompt}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.ws_connect(base_url + '/ws') as ws:
            started = time.monotonic()
            async with session.post(base_url + '/api/danmaku', json={'content': result['prompt'], 'user_name': 'Latency check', 'user_id': 990002}) as response:
                response.raise_for_status()
            text = ''
            complete = False
            audio_task = None

            async def first_audio(url):
                async with session.get(base_url + url) as response:
                    response.raise_for_status()
                    async for chunk in response.content.iter_any():
                        if chunk:
                            result['first_audio_ms'] = round((time.monotonic() - started) * 1000)
                            return
                raise RuntimeError('empty audio stream')

            deadline = started + 55
            while time.monotonic() < deadline:
                if complete and audio_task and audio_task.done():
                    await audio_task
                    break
                try:
                    event = await asyncio.wait_for(ws.receive_json(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                kind = event.get('type')
                if kind in ('response_error', 'tts_error'):
                    raise RuntimeError(kind)
                if kind in ('response_start', 'response') and 'first_text_ms' not in result:
                    result['first_text_ms'] = round((time.monotonic() - started) * 1000)
                if kind == 'response_chunk':
                    text += event.get('text', '')
                if event.get('audio_url') and audio_task is None:
                    result['cached_audio'] = event['audio_url'].startswith('/audio/')
                    audio_task = asyncio.create_task(first_audio(event['audio_url']))
                if kind in ('response_end', 'response'):
                    complete = True
                    text = event.get('full_text') or event.get('text') or text
            if not complete or not audio_task:
                raise RuntimeError('no complete reply with voice')
            await audio_task
            result['reply'] = text.strip()
    return result


async def benchmark(base_url, label, runs):
    samples = [await measure(base_url, label, PROMPTS[index % len(PROMPTS)]) for index in range(runs)]
    result = samples[0] if runs == 1 else {
        'label': label, 'samples': samples,
        'median_first_text_ms': round(statistics.median(s['first_text_ms'] for s in samples)),
        'median_first_audio_ms': round(statistics.median(s['first_audio_ms'] for s in samples)),
        'cached_audio_count': sum(s['cached_audio'] for s in samples),
    }
    path = Path(__file__).resolve().parents[1] / 'data/qa' / f'latency-{label}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key,value in result.items() if key not in ('reply','prompt','samples')}, ensure_ascii=False))
    if runs > 1:
        print(json.dumps({'samples': [{k:v for k,v in sample.items() if k not in ('reply','prompt')} for sample in samples]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--label', choices=['before','after'], default='after')
    parser.add_argument('--runs', type=int, choices=range(1,11), default=1)
    args = parser.parse_args()
    asyncio.run(benchmark(args.base_url.rstrip('/'), args.label, args.runs))
