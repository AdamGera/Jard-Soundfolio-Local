"""Proxy-aware network transport and a separate loopback-only connector."""
import asyncio
from contextlib import contextmanager
import ctypes as c
from functools import lru_cache
import ipaddress
import os
import re
import socket
import ssl
import sys

import httpcore
import httpx
import truststore

from managed_preferences import UTF8, bind, core_foundation
from output_transform_policy import LOOPBACK_MESSAGE


@lru_cache(maxsize=1)
def _tls_context():
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


@lru_cache(maxsize=1)
def _system_api():
    native = c.CDLL("/System/Library/Frameworks/CFNetwork.framework/CFNetwork")
    cf = core_foundation()
    pointer = c.c_void_p
    return (
        bind(native, "CFNetworkCopySystemProxySettings", pointer),
        bind(native, "CFNetworkCopyProxiesForURL", pointer, pointer, pointer),
        bind(cf, "CFURLCreateWithBytes", pointer, pointer, c.c_char_p, c.c_long, c.c_uint32, pointer),
        bind(cf, "CFArrayGetCount", c.c_long, pointer),
        bind(cf, "CFArrayGetValueAtIndex", pointer, pointer, c.c_long),
        bind(cf, "CFDictionaryGetValue", pointer, pointer, pointer),
        bind(cf, "CFStringGetCString", c.c_bool, pointer, c.c_char_p, c.c_long, c.c_uint32),
        bind(cf, "CFNumberGetValue", c.c_bool, pointer, c.c_long, pointer),
        bind(cf, "CFRelease", None, pointer),
        tuple(pointer.in_dll(native, key) for key in ("kCFProxyTypeKey", "kCFProxyHostNameKey", "kCFProxyPortNumberKey")),
    )


@contextmanager
def _owned(pointer, release):
    try:
        yield pointer
    finally:
        if pointer:
            release(pointer)


def _system_proxy(url):
    if sys.platform != "darwin":
        return None
    settings, proxies, create, count, entry, get, string, number, release, keys = _system_api()
    type_key, host_key, port_key = keys

    def text(value):
        buffer = c.create_string_buffer(512)
        if value and string(value, buffer, len(buffer), UTF8):
            return buffer.value.decode()

    raw = str(url).encode()
    with _owned(settings(), release) as options, _owned(create(None, raw, len(raw), UTF8, None), release) as target:
        if not options or not target:
            return None
        with _owned(proxies(target, options), release) as choices:
            for index in range(count(choices) if choices else 0):
                row = entry(choices, index)
                kind = text(get(row, type_key))
                if kind == "kCFProxyTypeNone":
                    return None
                if kind not in ("kCFProxyTypeHTTP", "kCFProxyTypeHTTPS"):
                    continue
                host = text(get(row, host_key))
                port = c.c_int32()
                value = get(row, port_key)
                if host and value and number(value, 3, c.byref(port)) and 0 < port.value < 65536:
                    host = f"[{host}]" if ":" in host and not host.startswith("[") else host
                    return f"http://{host}:{port.value}"
    return None


def _environment(name):
    return (os.environ.get(name) or os.environ.get(name.lower()) or "").strip()


def _bypasses(host, exceptions):
    host = host.rstrip(".").lower()
    for name in re.split(r"[\s,]+", exceptions):
        if name == "*":
            return True
        name = name.removeprefix("*.").lstrip(".").lower()
        if name.startswith("["):
            name = name[1:].partition("]")[0]
        elif name.count(":") == 1 and name.rpartition(":")[2].isdigit():
            name = name.rpartition(":")[0]
        name = name.rstrip(".")
        if name and (host == name or host.endswith(f".{name}")):
            return True
    return False


def proxy_for(url):
    configured = _environment("HTTPS_PROXY" if url.scheme == "https" else "HTTP_PROXY") or _environment("ALL_PROXY")
    if configured and _bypasses(url.host, _environment("NO_PROXY")):
        return None
    location = configured or _system_proxy(url)
    if not location:
        return None
    proxy = httpx.URL(location if "://" in location else "http://" + location)
    return proxy if proxy.scheme in ("http", "https") and proxy.host else None


class ProxyTransport(httpx.AsyncBaseTransport):
    """Resolve each request, including redirects, with the current proxy settings."""

    def __init__(self):
        self.transports = {}

    async def handle_async_request(self, request):
        address = await asyncio.to_thread(proxy_for, request.url)
        transport = self.transports.get(address)
        if transport is None:
            proxy = httpx.Proxy(address, ssl_context=_tls_context() if address.scheme == "https" else None) if address else None
            transport = httpx.AsyncHTTPTransport(verify=_tls_context(), proxy=proxy, trust_env=False)
            self.transports[address] = transport
        return await transport.handle_async_request(request)

    async def aclose(self):
        await asyncio.gather(*(transport.aclose() for transport in self.transports.values()))


class LoopbackBackend(httpcore.AnyIOBackend):
    """Resolve once, then connect to a verified numeric address."""

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            async with asyncio.timeout(timeout):
                loop = asyncio.get_running_loop()
                resolved = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                addresses = tuple(dict.fromkeys(item[4][0] for item in resolved))
                if not addresses or any(not ipaddress.ip_address(ip).is_loopback for ip in addresses):
                    raise ValueError(LOOPBACK_MESSAGE)
                failure = None
                for address in addresses:
                    try:
                        return await super().connect_tcp(address, port, local_address=local_address,
                                                         socket_options=socket_options)
                    except httpcore.ConnectError as error:
                        failure = error
                raise failure
        except TimeoutError as error:
            raise httpcore.ConnectTimeout() from error


class _CoreStream(httpx.AsyncByteStream):
    def __init__(self, response):
        self.response = response

    async def __aiter__(self):
        async for chunk in self.response.aiter_stream():
            yield chunk

    async def aclose(self):
        await self.response.aclose()


class LoopbackTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.pool = httpcore.AsyncConnectionPool(ssl_context=_tls_context(), network_backend=LoopbackBackend())

    async def handle_async_request(self, request):
        url = request.url
        forwarded = httpcore.Request(
            request.method, httpcore.URL(scheme=url.raw_scheme, host=url.raw_host, port=url.port, target=url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        reply = await self.pool.handle_async_request(forwarded)
        return httpx.Response(reply.status, headers=reply.headers, stream=_CoreStream(reply), extensions=reply.extensions)

    async def aclose(self):
        await self.pool.aclose()
