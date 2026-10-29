"""Loopback controller boundary: Host + peer + same-origin + non-simple header.

This protects a local service from browser CSRF and DNS rebinding. It does not
authenticate other programs already running as the local user.
"""
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket


def _local(connection):
    peer = connection.client
    # testclient is Starlette's in-process transport, not a network address.
    return (peer and peer.host in {'127.0.0.1', '::1', 'testclient'}
            and connection.url.hostname in {'127.0.0.1', 'localhost', '::1'})


def _origin(connection):
    scheme = {'ws': 'http', 'wss': 'https'}.get(connection.url.scheme, connection.url.scheme)
    return f'{scheme}://{connection.headers.get("host", "")}'


def local_resource(request: Request):
    """Media elements cannot attach X-Studio-API; still enforce local origin."""
    if not _local(request):
        raise HTTPException(403, 'Use the controller on localhost or 127.0.0.1.')
    expected = _origin(request)
    origin = request.headers.get('origin')
    referer = request.headers.get('referer')
    if (origin not in (None, expected)
            or request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none')):
        raise HTTPException(403, 'Open this resource from the local studio.')
    if referer:
        parsed = urlsplit(referer)
        if f'{parsed.scheme}://{parsed.netloc}' != expected:
            raise HTTPException(403, 'Open this resource from the local studio.')


def local_request(request: Request):
    local_resource(request)
    if request.headers.get('x-studio-api') != '1':
        raise HTTPException(403, 'Use the local controller (X-Studio-API: 1).')


async def local_socket(socket: WebSocket):
    # Browser WebSockets always carry Origin. Never accept a missing/foreign
    # Origin: unlike fetch they cannot add our non-simple controller header.
    if (not _local(socket) or socket.headers.get('origin') != _origin(socket)
            or socket.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none')):
        await socket.close(code=1008)
        return False
    return True
