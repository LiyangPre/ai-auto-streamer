"""Exercise a running local server without an API key.

Offline profile: python main.py --config config.local-test.yaml
Then: python test_integration.py --base-url http://127.0.0.1:8001
"""
import argparse
import asyncio
import json
import sys

import aiohttp


async def main(base_url="http://127.0.0.1:8000", timeout=20):
    base_url = base_url.rstrip("/")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=3)) as response:
                health = await response.json()
                if response.status != 200 or not health.get("pipeline_running"):
                    print("FAIL: the local pipeline is not healthy")
                    return 1
            async with session.ws_connect(f"{base_url}/ws") as ws:
                print(f"[ws] connected: {base_url}")
                async with session.post(f"{base_url}/api/danmaku", json={
                    "content": "你好呀", "user_name": "local-test",
                }) as response:
                    if response.status != 200:
                        print(f"FAIL: message rejected (HTTP {response.status})")
                        return 1
                print("[api] test greeting accepted")
                deadline = asyncio.get_running_loop().time() + timeout
                saw_input = False
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    event = await asyncio.wait_for(ws.receive(), timeout=remaining)
                    if event.type != aiohttp.WSMsgType.TEXT:
                        break
                    data = json.loads(event.data)
                    kind = data.get("type")
                    if kind in {"status", "pong"}:
                        continue
                    print(f"[event] {kind}")
                    if kind == "danmaku" and data.get("user_name") == "local-test":
                        saw_input = True
                    if kind == "response" or (kind == "response_end" and not data.get("blocked")):
                        if saw_input and (data.get("text") or data.get("full_text")):
                            print("PASS: HTTP input -> WebSocket chat -> reply")
                            return 0
                        break
                print("FAIL: no complete reply received")
                return 1
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            print(f"FAIL: {type(error).__name__}. Start the test server first, then retry.")
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test HTTP input and WebSocket replies")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.base_url, args.timeout)))
