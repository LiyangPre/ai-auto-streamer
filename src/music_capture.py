"""Same-origin signaling for local, audio-only WebRTC music capture.

Audio travels directly between browsers; this server stores no captured media.
One controller publishes to at most eight non-preview output pages.
"""
import asyncio
import json
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect


class MusicCaptureHub:
    def __init__(self):
        self.source = None
        self.outputs = {}
        self.lock = asyncio.Lock()

    async def send(self, socket, payload):
        if socket is None:
            return
        try:
            await asyncio.wait_for(socket.send_json(payload), 2)
        except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
            pass

    async def connect(self, socket: WebSocket):
        # Browser source/console must use the same origin as their own server.
        origin = socket.headers.get('origin', '')
        expected = ('https' if socket.url.scheme == 'wss' else 'http') + '://' + socket.headers.get('host', '')
        role = socket.query_params.get('role')
        if origin != expected or role not in {'source', 'output'}:
            await socket.close(code=1008)
            return
        await socket.accept()
        peer = uuid4().hex
        async with self.lock:
            if (role == 'source' and self.source) or (role == 'output' and len(self.outputs) >= 8):
                await self.send(socket, {'type': 'error', 'reason': 'busy'})
                await socket.close(code=1008)
                return
            if role == 'source':
                self.source = socket
            else:
                self.outputs[peer] = socket
            await self.send(socket, {'type': 'welcome', 'id': peer})
            if role == 'source':
                for output_id in self.outputs:
                    await self.send(socket, {'type': 'receiver', 'id': output_id})
            elif self.source:
                await self.send(self.source, {'type': 'receiver', 'id': peer})
        try:
            while True:
                raw = await socket.receive_text()
                if len(raw) > 64000:
                    await socket.close(code=1009)
                    break
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('Invalid message')
                kind = data.get('type')
                sdp = data.get('sdp')
                expected_kind = 'offer' if role == 'source' else 'answer'
                if kind != expected_kind or not isinstance(sdp, str) or 'm=audio ' not in sdp or 'm=video ' in sdp:
                    raise ValueError('Only audio session descriptions are allowed')
                if role == 'source':
                    target = data.get('id')
                    if isinstance(target, str):
                        await self.send(self.outputs.get(target), {'type': 'offer', 'sdp': sdp})
                else:
                    await self.send(self.source, {'type': 'answer', 'id': peer, 'sdp': sdp})
        except WebSocketDisconnect:
            pass
        except (ValueError, RuntimeError):
            await socket.close(code=1008)
        finally:
            async with self.lock:
                if role == 'source' and self.source is socket:
                    self.source = None
                    for output in self.outputs.values():
                        await self.send(output, {'type': 'source-off'})
                elif role == 'output':
                    self.outputs.pop(peer, None)
                    await self.send(self.source, {'type': 'receiver-left', 'id': peer})
