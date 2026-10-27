"""BigModel PCM streaming adapted to the shared browser MP3 player."""
import base64
import json

import lameenc


async def glm_audio_chunks(client, text, config, key):
    if not key:
        raise ValueError("GLM voice key is not configured")
    if len(text) > 1024:
        raise ValueError("GLM voice input exceeds 1024 characters")
    body = {"model": "glm-tts", "input": text, "voice": config.glm_voice,
            "stream": True, "response_format": "pcm", "encode_format": "base64",
            "speed": 1 + int(config.rate.rstrip("%")) / 100,
            "volume": 1 + int(config.volume.rstrip("%")) / 100}
    encoder = None
    sample_rate = None
    pending = b""
    total = 0
    completed = False
    async with client.stream("POST", "https://open.bigmodel.cn/api/paas/v4/audio/speech",
                             headers={"Authorization": f"Bearer {key}"}, json=body,
                             timeout=config.timeout) as response:
        if response.status_code != 200:
            raise ValueError(f"GLM voice HTTP {response.status_code}")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                if line.lstrip().startswith("{"):
                    raise ValueError("GLM returned a non-stream response")
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            if event.get("error"):
                # Never include provider bodies or credentials in surfaced errors.
                raise ValueError("GLM voice request was rejected")
            for choice in event.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    rate = delta.get("return_sample_rate", sample_rate or 24000)
                    if rate not in (16000, 22050, 24000, 32000, 44100, 48000):
                        raise ValueError("GLM returned an unsupported sample rate")
                    if encoder is None:
                        sample_rate = rate
                        encoder = lameenc.Encoder()
                        encoder.set_bit_rate(96)
                        encoder.set_in_sample_rate(rate)
                        encoder.set_channels(1)
                        encoder.set_quality(5)
                    elif rate != sample_rate:
                        raise ValueError("GLM changed sample rate during speech")
                    pcm = base64.b64decode(delta["content"], validate=True)
                    total += len(pcm)
                    if total > 8 * 1024 * 1024:
                        raise ValueError("GLM PCM stream exceeds buffer limit")
                    # PCM16 mono samples may cross SSE message boundaries.
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
                        raise ValueError("GLM audio generation did not complete")
                    completed = True
            if completed:
                break
    if not completed or not total or pending or encoder is None:
        raise ValueError("GLM audio stream ended before completion")
    final = bytes(encoder.flush())
    if final:
        yield final
