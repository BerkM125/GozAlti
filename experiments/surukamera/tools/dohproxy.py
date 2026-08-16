"""Local HTTP CONNECT proxy with DNS-over-TCP resolution.

This machine's network blocks outbound UDP/53, so ordinary getaddrinfo()
fails everywhere. TCP/53 works. This proxy resolves hostnames itself via
DNS-over-TCP to public resolvers and tunnels bytes, so any client that
honors HTTP(S)_PROXY (pip, httpx, node, Chromium via --proxy-server)
gets working networking with zero system config changes.

Stdlib only — must run before anything can be pip-installed.

Usage:  python tools/dohproxy.py [port]     (default 18888)
"""
import socket
import struct
import sys
import threading
import time

RESOLVERS = [("8.8.8.8", 53), ("1.1.1.1", 53), ("8.8.4.4", 53)]
CACHE: dict[str, tuple[float, str]] = {}
CACHE_TTL = 300.0
CACHE_LOCK = threading.Lock()


def _build_query(name: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        struct.pack("B", len(p)) + p.encode("ascii") for p in name.split(".")
    ) + b"\x00"
    return header + qname + struct.pack(">HH", 1, 1)  # A, IN


def _skip_name(buf: bytes, off: int) -> int:
    while True:
        length = buf[off]
        if length == 0:
            return off + 1
        if length & 0xC0:  # compression pointer
            return off + 2
        off += 1 + length


def _parse_a_records(buf: bytes) -> list[str]:
    qid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        off = _skip_name(buf, off) + 4
    ips = []
    for _ in range(an):
        off = _skip_name(buf, off)
        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(buf[off:off + 4]))
        off += rdlen
    return ips


def resolve(name: str) -> str:
    try:
        socket.inet_aton(name)
        return name  # already an IP literal
    except OSError:
        pass
    now = time.time()
    with CACHE_LOCK:
        hit = CACHE.get(name)
        if hit and hit[0] > now:
            return hit[1]
    query = _build_query(name, qid=int(now) & 0xFFFF)
    for resolver in RESOLVERS:
        try:
            with socket.create_connection(resolver, timeout=4) as s:
                s.sendall(struct.pack(">H", len(query)) + query)
                raw = b""
                while len(raw) < 2:
                    raw += s.recv(2 - len(raw))
                (rlen,) = struct.unpack(">H", raw)
                buf = b""
                while len(buf) < rlen:
                    chunk = s.recv(rlen - len(buf))
                    if not chunk:
                        break
                    buf += chunk
            ips = _parse_a_records(buf)
            if ips:
                with CACHE_LOCK:
                    CACHE[name] = (now + CACHE_TTL, ips[0])
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


def _read_headers(conn: socket.socket) -> bytes:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return buf
        buf += chunk
    return buf


def handle(conn: socket.socket) -> None:
    try:
        head = _read_headers(conn)
        if not head:
            conn.close()
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        method, target, _version = request_line.split(" ", 2)

        if method == "CONNECT":
            host, _, port = target.partition(":")
            try:
                upstream = socket.create_connection(
                    (resolve(host), int(port or 443)), timeout=15
                )
            except OSError as exc:
                conn.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\n\r\n" + str(exc).encode()[:200]
                )
                conn.close()
                return
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            threading.Thread(target=_pipe, args=(conn, upstream), daemon=True).start()
            _pipe(upstream, conn)
            return

        # Absolute-form plain HTTP (e.g. GET http://host/path)
        if target.startswith("http://"):
            rest = target[7:]
            hostport, slash, path = rest.partition("/")
            host, _, port = hostport.partition(":")
            try:
                upstream = socket.create_connection(
                    (resolve(host), int(port or 80)), timeout=15
                )
            except OSError as exc:
                conn.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\n\r\n" + str(exc).encode()[:200]
                )
                conn.close()
                return
            rewritten = head.replace(
                request_line.encode("latin-1"),
                f"{method} /{path} HTTP/1.1".encode("latin-1"),
                1,
            )
            upstream.sendall(rewritten)
            threading.Thread(target=_pipe, args=(conn, upstream), daemon=True).start()
            _pipe(upstream, conn)
            return

        conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        conn.close()
    except Exception:
        try:
            conn.close()
        except OSError:
            pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18888
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(128)
    print(f"dohproxy listening on 127.0.0.1:{port}", flush=True)
    while True:
        conn, _addr = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
