r"""Repeat real GLM text checks without TTS, broadcasting, or settings changes.

Uses the saved local credentials, but never writes keys or raw exception text.
Run from the project root: .venv\Scripts\python.exe scripts/diagnose_glm.py
"""
import argparse
import asyncio
import json
import logging
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api_settings import ConnectionStore, connection_path
from src.characters import library_path, load_library
from src.config import Config
from src.llm import CallParams, LLMProcessor
from src.provider_network import failure, route
from src.studio import load_saved, settings_path


def safe_causes(error):
    causes, seen = [], set()
    while error is not None and id(error) not in seen and len(causes) < 10:
        seen.add(id(error))
        item = {"type": type(error).__name__}
        if isinstance(error, ssl.SSLCertVerificationError):
            item["verify_code"] = error.verify_code
        if isinstance(error, ssl.SSLError):
            item["ssl_reason"] = getattr(error, "reason", None)
        causes.append(item)
        error = error.__cause__ or error.__context__
    return causes


async def check(processor, index, streaming):
    nonce = f"GLM-{index + 1:02d}"
    prompts = [
        f"连接测试，请只回复这段编号：{nonce}",
        "今天工作有点累了，能用一句温柔又俏皮的话安慰我吗？",
        "请用中文写约一百五十字的小故事：Astra、Claude和DeepSeek一起做早餐，结尾要好笑。",
    ]
    text = prompts[index % 3]
    params = CallParams(max_tokens=192 if index % 3 == 2 else 96)
    result = {"case": index + 1, "stream": streaming, "ok": False}
    started = time.monotonic()
    response = None
    output, reasons, chunks = "", [], 0
    events = []

    async def trace(name, info):
        if len(events) < 64:
            events.append({"event": name, "at_ms": round((time.monotonic() - started) * 1000)})

    async def record_request(request):
        request.extensions["trace"] = trace

    hooks = processor.client._client.event_hooks["request"]
    hooks.append(record_request)
    try:
        async with asyncio.timeout(30):
            response = await processor.client.chat.completions.create(
                messages=processor._build_messages(text, params),
                stream=streaming,
                **processor._request_kwargs(params),
            )
            result["headers_ms"] = round((time.monotonic() - started) * 1000)
            if streaming:
                async for chunk in response:
                    for choice in chunk.choices:
                        content = choice.delta.content
                        if content:
                            if not output:
                                result["first_text_ms"] = round((time.monotonic() - started) * 1000)
                            output += content
                            chunks += 1
                        if choice.finish_reason:
                            reasons.append(choice.finish_reason)
            else:
                output = response.choices[0].message.content or ""
                reasons = [response.choices[0].finish_reason]
                usage = response.usage
                if usage:
                    result["tokens"] = {"input": usage.prompt_tokens, "output": usage.completion_tokens}
            result.update(ok=bool(output.strip()), characters=len(output), text=output[:250],
                          chunks=chunks, finish_reasons=reasons)
            if index % 3 == 0:
                result["expected_id_received"] = nonce in output
    except Exception as error:
        result.update(failure(error))
        result["causes"] = safe_causes(error)
        if output:
            result["partial_characters"] = len(output)
    finally:
        hooks.remove(record_request)
        if streaming and response is not None:
            await response.close()
    result["transport_events"] = events
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


async def diagnose(mode, config, count, output):
    llm = config.llm.model_copy(deep=True)
    llm.network_mode = mode
    processor = LLMProcessor(llm, config.character)
    try:
        for index in range(count):
            result = await check(processor, index, index >= count // 2)
            result.update(mode=mode, route=route(mode)[0])
            output.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await processor.close()


async def main(args):
    config = Config.load(args.config)
    studio_path = settings_path(args.config)
    load_saved(config, studio_path)
    characters = load_library(config, library_path(studio_path))
    characters.profiles[characters.default_id].settings(characters.scene).apply(config)
    store = ConnectionStore(connection_path(studio_path), config)
    store.load()
    store.apply(config)
    if config.llm.provider != "glm" or not config.llm.api_key_configured:
        raise SystemExit("Configure GLM with a key in the local controller first.")
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "model": config.llm.model, "thinking": config.llm.enable_thinking,
              "broadcast": False, "tts": False, "results": []}
    print(json.dumps({k: v for k, v in report.items() if k != "results"}), flush=True)
    await asyncio.gather(*(diagnose(mode, config, args.count, report["results"]) for mode in args.modes))
    report["summary"] = {
        mode: {"passed": sum(r["ok"] for r in report["results"] if r["mode"] == mode),
               "total": sum(r["mode"] == mode for r in report["results"])} for mode in args.modes
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "report": str(args.output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--count", type=int, choices=range(2, 13), default=6, help="Checks per route")
    parser.add_argument("--modes", nargs="+", choices=("direct", "auto"), default=["direct", "auto"])
    parser.add_argument("--output", type=Path, default=ROOT / "data/qa/glm-repeated-text.json")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    asyncio.run(main(parser.parse_args()))
