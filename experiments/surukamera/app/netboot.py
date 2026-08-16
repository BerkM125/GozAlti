"""Self-contained networking bootstrap.

This machine's network blocks outbound UDP/53 (all DNS servers time out),
while TCP works fine. Rather than requiring system config changes, every
outbound HTTP call in this app goes through an in-process CONNECT proxy
that resolves hostnames itself via DNS-over-TCP to public resolvers.

Works identically on healthy networks, so it is always on.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

import httpx

RESOLVERS = [("8.8.8.8", 53), ("1.1.1.1", 53), ("8.8.4.4", 53)]
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 300.0
_LOCK = threading.Lock()

_proxy_port: int | None = None
_proxy_lock = threading.Lock()

USER_AGENT = (
    "SuruKamera/0.1 (hackathon research client; road-geometry study; "
    "contact: berkanm@uw.edu)"
)


def _build_query(name: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        struct.pack("B", len(p)) + p.encode("ascii") for p in name.split(".")
    ) + b"\x00"
    return header + qname + struct.pack(">HH", 1, 1)


def _skip_name(buf: bytes, off: int) -> int:
    while True:
        length = buf[off]
        if length == 0:
            return off + 1
        if length & 0xC0:
            return off + 2
        off += 1 + length


def _parse_a_records(buf: bytes) -> list[str]:
    _qid, _flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        off = _skip_name(buf, off) + 4
    ips: list[str] = []
    for _ in range(an):
        off = _skip_name(buf, off)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(buf[off:off + 4]))
        off += rdlen
    return ips


def resolve(name: str) -> str:
    """Resolve a hostname to an IPv4 address via DNS-over-TCP."""
    try:
        socket.inet_aton(name)
        return name
    except OSError:
        pass
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] > now:
            return hit[1]
    query = _build_query(name, qid=int(now) & 0xFFFF)
    for resolver in RESOLVERS:
        try:
            with socket.create_connection(resolver, timeout=4) as s:
                s.sendall(struct.pack(">H", len(query)) + query)
                raw = b""
                while len(raw) < 2:
                    chunk = s.recv(2 - len(raw))
                    if not chunk:
                        raise OSError("short read")
                    raw += chunk
                (rlen,) = struct.unpack(">H", raw)
                buf = b""
                while len(buf) < rlen:
                    chunk = s.recv(rlen - len(buf))
                    if not chunk:
                        break
                    buf += chunk
            ips = _parse_a_records(buf)
            if ips:
                with _LOCK:
                    _CACHE[name] = (now + _CACHE_TTL, ips[0])
                return ips[0]
        except OSError:
            continue
    raise OSError(f"DNS-over-TCP resolution failed for {name!r}")


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(conn: socket.socket) -> None:
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            buf += chunk
        request_line = buf.split(b"\r\n", 1)[0].decode("latin-1")
        method, target, _version = request_line.split(" ", 2)
        if method != "CONNECT":
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()
            return
        host, _, port = target.partition(":")
        try:
            upstream = socket.create_connection(
                (resolve(host), int(port or 443)), timeout=15
            )
        except OSError as exc:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n" + str(exc).encode()[:200])
            conn.close()
            return
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        threading.Thread(target=_pipe, args=(conn, upstream), daemon=True).start()
        _pipe(upstream, conn)
    except Exception:
        try:
            conn.close()
        except OSError:
            pass


def ensure_proxy() -> str:
    """Start (once) the in-process CONNECT proxy; return its URL."""
    global _proxy_port
    with _proxy_lock:
        if _proxy_port is not None:
            return f"http://127.0.0.1:{_proxy_port}"
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(128)
        _proxy_port = srv.getsockname()[1]

        def loop() -> None:
            while True:
                conn, _addr = srv.accept()
                threading.Thread(target=_handle, args=(conn,), daemon=True).start()

        threading.Thread(target=loop, daemon=True, name="dohproxy").start()
        return f"http://127.0.0.1:{_proxy_port}"


def make_client(**kwargs) -> httpx.Client:
    kwargs.setdefault("headers", {})["User-Agent"] = USER_AGENT
    kwargs.setdefault("timeout", 20.0)
    kwargs.setdefault("follow_redirects", True)
    return httpx.Client(proxy=ensure_proxy(), **kwargs)


def make_async_client(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("headers", {})["User-Agent"] = USER_AGENT
    kwargs.setdefault("timeout", 20.0)
    kwargs.setdefault("follow_redirects", True)
    return httpx.AsyncClient(proxy=ensure_proxy(), **kwargs)
