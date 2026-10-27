"""MiMo preset-voice SSE audio, with separate performance instructions."""
import base64
import json

import lameenc


async def mimo_audio_chunks(client, text, config, key):
    if not key:
        raise ValueError("MiMo voice key is not configured")
    messages = [{"role": "user", "content": config.mimo_style}] if config.mimo_style.strip() else []
    messages.append({"role": "assistant", "content": text})
    body = {"model": "mimo-v2.5-tts", "stream": True,
            "messages": messages,
            "audio": {"format": "pcm16", "voice": config.mimo_voice}}
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(24000)
    encoder.set_channels(1)
    encoder.set_quality(5)
    pending = b""
    total = 0
    completed = False
    async with client.stream("POST", "https://api.xiaomimimo.com/v1/chat/completions",
                             headers={"Authorization": f"Bearer {key}"}, json=body,
                             timeout=config.timeout) as response:
        if response.status_code != 200:
            raise ValueError(f"MiMo voice HTTP {response.status_code}")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                if line.lstrip().startswith("{"):
                    raise ValueError("MiMo returned a non-stream response")
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event.get("error"):
                raise ValueError("MiMo voice request was rejected")
            for choice in event.get("choices", []):
                audio = (choice.get("delta") or {}).get("audio") or {}
                if audio.get("data"):
                    pcm = base64.b64decode(audio["data"], validate=True)
                    total += len(pcm)
                    if total > 8 * 1024 * 1024:
                        raise ValueError("MiMo PCM stream exceeds buffer limit")
                    # The API returns 24 kHz PCM16LE mono. Keep split samples intact.
                    pcm = pending + pcm
                    boundary = len(pcm) - len(pcm) % 2
                    pending = pcm[boundary:]
                    if boundary:
                        chunk = bytes(encoder.encode(pcm[:boundary]))
                        if chunk:
                            yield chunk
                reason = choice.get("finish_reason")
                if reason:
                    if reason != "stop":
                        raise ValueError("MiMo audio generation did not complete")
                    completed = True
            if completed:
                break
    if not completed or not total or pending:
        raise ValueError("MiMo audio stream ended before completion")
    final = bytes(encoder.flush())
    if final:
        yield final
