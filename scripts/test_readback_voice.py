r"""One real conversation on the running LOCAL studio (uses its LLM/TTS provider).

Run: .\.venv\Scripts\python.exe scripts/test_readback_voice.py
Sends only a synthetic test chat. Never prints credentials or contacts Bilibili.
"""
import asyncio
import json
import time
import sys
from pathlib import Path

import aiohttp


async def main():
    base = 'http://127.0.0.1:8000'
    prompt = '主播，请问今天累了怎么办？'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=55)) as session:
        async with session.get(base + '/api/studio') as response:
            studio = await response.json()
        if not studio['settings']['reply']['read_chat']:
            raise RuntimeError('Enable read-selected-chat in Replies first')
        async with session.ws_connect(base + '/ws') as socket:
            started = time.monotonic()
            async with session.post(base + '/api/danmaku', json={'content': prompt, 'user_name': '语音测试', 'user_id': 990002}) as response:
                response.raise_for_status()
            segments, downloads = [], []

            async def download(url):
                async with session.get(base + url) as response:
                    response.raise_for_status()
                    first = await response.content.readany()
                    first_ms = round((time.monotonic() - started) * 1000)
                    data = first + await response.read()
                    if len(data) < 100:
                        raise RuntimeError('Empty speech audio')
                    return first_ms, len(data)

            try:
                async with asyncio.timeout(55):
                    while True:
                        event = await socket.receive_json()
                        if event['type'] in {'response_error', 'tts_error', 'moderation', 'reply_dropped'}:
                            raise RuntimeError(event['type'])
                        if event['type'] == 'audio_segment':
                            segments.append({'text': event['text'], 'url': event['audio_url']})
                            downloads.append(asyncio.create_task(download(event['audio_url'])))
                        if event['type'] == 'response_end':
                            answer = event['full_text']
                            break
                    audio = await asyncio.gather(*downloads)
                spoken = ''.join(s['text'] for s in segments)
                report = {'prompt': prompt, 'answer': answer, 'spoken_text': spoken,
                          'voice_provider': studio['settings']['voice']['provider'],
                          'first_audio_ms': min(x[0] for x in audio), 'audio_bytes': sum(x[1] for x in audio),
                          'segments': segments}
                path = Path(__file__).resolve().parents[1] / 'data/qa/readback-voice-test.json'
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(report, ensure_ascii=False, indent=2))
                assert segments and ''.join(spoken.split()) == ''.join(('今天累了怎么办？' + answer).split()), 'Speech readback must prefix the unchanged answer exactly once'
                print('PASS: original answer preserved; chat core spoken exactly once; real audio received')
            finally:
                for task in downloads:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*downloads, return_exceptions=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
