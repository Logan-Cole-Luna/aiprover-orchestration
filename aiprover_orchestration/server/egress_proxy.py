"""A CONNECT proxy that relays TLS to the hosted model APIs.

Compute nodes without internet access reach their login node over SSH. A run
job (server/slurm.py) starts this proxy on the login node through an SSH
session that also forwards a local port to the proxy's Unix socket, and sets
HTTPS_PROXY to that port. The proxy relays TLS without reading it, accepts only
CONNECT to port 443 of a host that --allow names, and exits when its stdin
closes, that is, when the SSH session ends.
"""

import argparse
import asyncio
import fnmatch
import logging
import os
from pathlib import Path

# Host patterns that CONNECT may name; set by --allow.
ALLOWED_HOSTS: list[str] = []
HEADER_LIMIT = 16 * 1024
IDLE_SECONDS = 15 * 60
REASONS = {
    200: "Connection established",
    403: "Forbidden",
    405: "CONNECT only",
    502: "Bad Gateway",
}

logger = logging.getLogger("egress_proxy")


def allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(fnmatch.fnmatchcase(host, pattern) for pattern in ALLOWED_HOSTS)


def parse_connect(head: bytes) -> tuple[str, int] | None:
    """Host and port of a CONNECT request head; None for any other request."""
    try:
        line = head.split(b"\r\n", 1)[0].decode("ascii")
        method, target, _version = line.split(" ")
        host, _, port = target.rpartition(":")
    except ValueError:
        return None
    if method != "CONNECT" or not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


async def reply(writer: asyncio.StreamWriter, status: int) -> None:
    length = "" if status == 200 else "Content-Length: 0\r\n"
    writer.write(f"HTTP/1.1 {status} {REASONS[status]}\r\n{length}\r\n".encode())
    await writer.drain()


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_SECONDS):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        target = parse_connect(head)
        if target is None:
            return await reply(writer, 405)
        host, port = target
        if port != 443 or not allowed(host):
            logger.warning(f"refused CONNECT {host}:{port}")
            return await reply(writer, 403)
        try:
            upstream = await asyncio.wait_for(asyncio.open_connection(host, port), 30)
        except (OSError, asyncio.TimeoutError):
            return await reply(writer, 502)
        await reply(writer, 200)
        await asyncio.gather(relay(reader, upstream[1]), relay(upstream[0], writer))
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
        pass
    finally:
        writer.close()


async def stdin_closed() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, os.fdopen(0, "rb"))
    while await reader.read(4096):
        pass


async def serve(socket_path: Path, watch_stdin: bool = False) -> None:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, str(socket_path), limit=HEADER_LIMIT)
    os.chmod(socket_path, 0o600)
    try:
        async with server:
            await (stdin_closed() if watch_stdin else server.serve_forever())
    finally:
        socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unix", type=Path, required=True, help="socket to listen on")
    parser.add_argument("--exit-on-stdin-eof", action="store_true")
    parser.add_argument("--allow", action="append", default=[], help="host pattern, such as *.example.com")
    arguments = parser.parse_args()
    ALLOWED_HOSTS.extend(arguments.allow)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve(arguments.unix, arguments.exit_on_stdin_eof))


if __name__ == "__main__":
    main()
