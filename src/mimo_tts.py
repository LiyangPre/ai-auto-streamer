"""MiMo voice design/preset audio; synthesis text and direction stay separate."""
import base64
import json

import lameenc
from .provider_network import check_response


async def mimo_audio_chunks(client, text, config, key, *, max_pcm_bytes=8 * 1024 * 1024):
    if not key:
        raise ValueError("MiMo voice key is not configured")
    if config.mimo_model == 'mimo-v2.5-tts-voicedesign' and not config.mimo_style.strip():
        raise ValueError('Voice design requires a voice description')
    messages = [{"role": "user", "content": config.mimo_style}] if config.mimo_style.strip() else []
    messages.append({"role": "assistant", "content": text})
    body = {"model": config.mimo_model, "stream": True,
            "messages": messages,
            "audio": {"format": "pcm16"}}
    if config.mimo_model == 'mimo-v2.5-tts':
        body['audio']['voice'] = config.mimo_voice
    else:
        body['audio']['optimize_text_preview'] = False  # Preserve the supplied story/reply verbatim.
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(24000)
    encoder.set_channels(1)
    encoder.set_quality(5)
    pending = b""
    total = 0
    completed = False
    async with client.stream("POST", config.mimo_base_url.rstrip('/') + '/chat/completions',
                             headers={"Authorization": f"Bearer {key}"}, json=body,
                             timeout=config.timeout) as response:
        await check_response(response)
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
                    if total > max_pcm_bytes:
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
