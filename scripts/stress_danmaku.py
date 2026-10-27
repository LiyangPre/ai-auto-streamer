"""Stress test: feed N simulated danmaku within T seconds and observe pipeline behavior.

Usage:
    python scripts/stress_danmaku.py [count=50] [duration=20] [base_url]

Requires the server to be running (python main.py).
"""
import asyncio
import json
import sys
import time

import aiohttp

# 50 simulated danmaku (user, content). Includes filter edge cases:
#  - #12/#13 too short (min_length=2)  -> dropped by filter
#  - #14 too long (max_length=50)      -> dropped by filter
DANMAKU = [
    ("小明", "你好呀"),
    ("阿紫", "主播好可爱"),
    ("路人甲", "哈喽哈喽"),
    ("甜甜", "今天天气怎么样"),
    ("老铁", "你叫什么名字"),
    ("小鱼", "在吗在吗"),
    ("喵喵", "哇好厉害"),
    ("大白", "唱首歌吧"),
    ("星星", "太棒了"),
    ("黑粉", "你好笨啊"),
    ("杠精", "讨厌你"),
    ("围观", "嗯"),
    ("吃瓜", "好"),
    ("长篇大论", "这是一条非常非常长的弹幕测试内容用来验证最大长度过滤是否生效希望它超过五十个字符被过滤掉"),
    ("小红", "谢谢关注"),
    ("阿伟", "早上好"),
    ("夜猫", "晚上好"),
    ("学霸", "为什么天是蓝的"),
    ("好奇", "真的假的"),
    ("惊讶君", "天哪不可思议"),
    ("开心果", "哈哈哈笑死我了"),
    ("难过侠", "有点想哭"),
    ("生气包", "气死我了"),
    ("冷静姐", "别激动"),
    ("提问机", "能讲个笑话吗"),
    ("点歌台", "来首儿歌"),
    ("老观众", "又来看你了"),
    ("新粉丝", "第一次来"),
    ("路人乙", "路过"),
    ("打赏哥", "送你礼物"),
    ("小明", "再来一次"),
    ("阿紫", "加油加油"),
    ("甜甜", "喜欢你"),
    ("大白", "抱抱"),
    ("星星", "晚安"),
    ("喵喵", "摸摸头"),
    ("小鱼", "666"),
    ("老铁", "牛啊"),
    ("黑粉", "就这"),
    ("杠精", "不服"),
    ("围观", "哇"),
    ("吃瓜", "惊了"),
    ("小红", "感谢"),
    ("阿伟", "辛苦了"),
    ("夜猫", "还不睡吗"),
    ("学霸", "求解答"),
    ("好奇", "怎么做到的"),
    ("开心果", "逗死我了"),
    ("难过侠", "呜呜呜"),
    ("生气包", "哼"),
]


async def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8000"
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    msgs = (DANMAKU * ((count // len(DANMAKU)) + 1))[:count]
    interval = duration / count

    stats = {"sent": 0, "errors": 0, "queue_updates": [], "replies": 0, "thinking": 0}

    async def feeder(session):
        t0 = time.time()
        for i, (user, content) in enumerate(msgs):
            target = t0 + i * interval
            delay = target - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                async with session.post(
                    f"{base}/api/danmaku",
                    json={"content": content, "user_name": user},
                ) as r:
                    if r.status == 200:
                        stats["sent"] += 1
                    else:
                        stats["errors"] += 1
            except Exception:
                stats["errors"] += 1

    async def watcher(session):
        start = time.time()
        try:
            async with session.ws_connect(ws_url) as ws:
                while time.time() - start < duration + 10:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(msg.data)
                    t = round(time.time() - start, 1)
                    if data["type"] == "queue_update":
                        stats["queue_updates"].append((t, data["count"]))
                        print(f"[{t:>5}s] queue_update  window_count={data['count']}")
                    elif data["type"] == "thinking_start":
                        stats["thinking"] += 1
                    elif data["type"] == "response_end":
                        stats["replies"] += 1
                        print(f"[{t:>5}s] REPLY #{stats['replies']}: {data.get('full_text','')[:40]}")
                    elif data["type"] == "response":
                        stats["replies"] += 1
                        print(f"[{t:>5}s] REPLY #{stats['replies']}: {data.get('text','')[:40]}")
        except Exception as e:
            print("watcher error:", e)

    async with aiohttp.ClientSession() as session:
        watcher_task = asyncio.create_task(watcher(session))
        await asyncio.sleep(0.5)  # let watcher connect first
        t0 = time.time()
        await feeder(session)
        feed_elapsed = time.time() - t0
        await watcher_task

    total_windowed = sum(c for _, c in stats["queue_updates"])
    overflow_dropped = stats["sent"] - total_windowed
    print("\n================ STRESS TEST SUMMARY ================")
    print(f"target          : {count} danmaku in {duration}s")
    print(f"feed elapsed    : {feed_elapsed:.1f}s  (interval {interval:.2f}s)")
    print(f"sent ok / err   : {stats['sent']} / {stats['errors']}")
    print(f"within {duration:.0f}s?     : {'YES' if feed_elapsed <= duration else 'NO'}")
    print(f"queue windows   : {len(stats['queue_updates'])}  (messages seen in windows: {total_windowed})")
    print(f"overflow dropped: {overflow_dropped}  (max_queue_size truncation)")
    print(f"thinking cycles : {stats['thinking']}")
    print(f"replies emitted : {stats['replies']}  (filtered down from {count} inputs)")
    print("=====================================================")


if __name__ == "__main__":
    asyncio.run(main())
