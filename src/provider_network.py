"""Explicit HTTP routing and credential-free provider diagnostics."""
import asyncio
import os
import socket
import ssl
from urllib.parse import urlsplit

import httpx


def windows_proxy():
    if os.name != 'nt':
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if not winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                return None
            value = winreg.QueryValueEx(key, 'ProxyServer')[0].strip()
        if '=' in value:
            parts = dict(part.split('=', 1) for part in value.split(';') if '=' in part)
            value = parts.get('https') or parts.get('http') or ''
        if not value:
            return None
        value = value if '://' in value else 'http://' + value
        if urlsplit(value).scheme in {'http', 'https'}:
            return value
    except (OSError, ValueError):
        pass
    return None


def route(mode):
    if mode == 'direct':
        return 'direct', {'trust_env': False}
    if mode == 'environment' or any(os.environ.get(k) for k in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')):
        return 'environment', {'trust_env': True}
    proxy = windows_proxy()
    if proxy:
        return 'windows_proxy', {'trust_env': False, 'proxy': proxy}
    return 'direct', {'trust_env': False}


def http_client(mode, timeout):
    _, options = route(mode)
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(8, timeout)), **options)


class ProviderResponseError(RuntimeError):
    def __init__(self, status_code, category=None):
        self.status_code = status_code
        self.category = category
        super().__init__('Provider request rejected')


async def check_response(response):
    if response.status_code == 200:
        return
    # Inspect a bounded error body only to classify it. Never log or return it.
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk[:4096-len(body)])
        if len(body) >= 4096:
            break
    content = body.decode('utf-8', errors='replace').lower()
    category = None
    if any(word in content for word in ('unsupported_country', 'unsupported_region', 'region_not_supported', '地区不支持', '地域限制')):
        category = 'region'
    raise ProviderResponseError(response.status_code, category)


def failure(error):
    """Classify causes without echoing URLs, keys, headers or response bodies."""
    chain=[]
    current=error
    while current is not None and len(chain)<8 and current not in chain:
        chain.append(current);current=current.__cause__ or current.__context__
    status=next((getattr(e,'status_code',None) for e in chain if getattr(e,'status_code',None)),None)
    if any(getattr(e,'category',None)=='region' for e in chain):
        code,message='region','Provider rejected this region; check the account region and its matching Base URL / API key.'
    elif status in (401,403):
        code,message='authentication',f'Provider access rejected (HTTP {status}); check the key, account region and endpoint.'
    elif status==429:
        code,message='quota','Provider quota or rate limit reached (HTTP 429).'
    elif status and status>=500:
        code,message='provider_service',f'Provider service failed (HTTP {status}); retry shortly.'
    elif status:
        code,message='provider_http',f'Provider returned HTTP {status}; check model access and endpoint.'
    elif any(isinstance(e,socket.gaierror) for e in chain):
        code,message='dns','DNS lookup failed; check the selected network route and DNS.'
    elif any(isinstance(e,ssl.SSLError) or 'CERTIFICATE_VERIFY_FAILED' in str(e) for e in chain):
        code,message='tls','TLS certificate or handshake failed; check the proxy certificate and system clock.'
    elif any(isinstance(e,httpx.ProxyError) for e in chain):
        code,message='proxy','Proxy connection failed; check the proxy app or select Direct.'
    elif any(isinstance(e,(httpx.TimeoutException,asyncio.TimeoutError,TimeoutError)) or type(e).__name__=='APITimeoutError' for e in chain):
        code,message='timeout','Provider request timed out; compare Direct and Auto network routes.'
    elif any(isinstance(e,(httpx.ConnectError,ConnectionError)) or type(e).__name__=='APIConnectionError' for e in chain):
        code,message='connection','Connection to provider failed before an HTTP response; check DNS, proxy and network route.'
    else:
        code,message='response','Provider returned incomplete or invalid data; retry the connection test.'
    return {'code':code,'message':message,'http_status':status}
