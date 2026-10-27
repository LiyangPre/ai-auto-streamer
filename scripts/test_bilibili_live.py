"""Bounded, read-only Bilibili receiver probe. Never starts a stream or posts chat."""
import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
from blivedm.clients.web import ROOM_INIT_URL
from blivedm.utils import USER_AGENT
from src.config import PlatformConfig
from src.danmaku import BilibiliDanmakuCollector, MessageQueue


def report(kind, **fields):
    print(json.dumps({'type': kind, **fields}, ensure_ascii=False), flush=True)


async def probe(room_id, seconds):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        try:
            async with session.get(ROOM_INIT_URL, params={'room_id': room_id},
                                   headers={'User-Agent': USER_AGENT}) as response:
                body = await response.json()
                data = body.get('data') or {}
                report('room', http_status=response.status, code=body.get('code'),
                       room_id=data.get('room_id'), title=data.get('title'),
                       live_status=data.get('live_status'))
        except Exception as error:
            report('room_error', error_type=type(error).__name__)

    queue = MessageQueue()
    collector = BilibiliDanmakuCollector(PlatformConfig(type='bilibili', room_id=room_id), queue)
    task = asyncio.create_task(collector.start())
    counts = Counter()
    authenticated = False
    previous = False
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline and not task.done():
            if collector.connected != previous:
                previous = collector.connected
                authenticated |= previous
                report('connection', authenticated=previous)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=.2)
            except asyncio.TimeoutError:
                continue
            counts[event.msg_type] += 1
            report('event', event_type=event.msg_type, user=event.user_name,
                   text=event.content, gift_name=event.gift_name, gift_num=event.gift_num)
            queue.task_done()
    finally:
        await collector.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    report('result', room_id=room_id, authenticated=authenticated,
           events=dict(counts), observation_seconds=seconds)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('room_id', type=int)
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if args.room_id <= 0 or not 1 <= args.seconds <= 120:
        parser.error('Use a positive room ID and 1–120 seconds.')
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(probe(args.room_id, args.seconds))
