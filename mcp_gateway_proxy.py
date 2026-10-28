#!/usr/bin/env python3
"""TCP forwarder for the local MCP endpoint.

Listens on Docker bridge gateway addresses (172.17.0.1, 172.18.0.1, ...) and
forwards to the MCP server bound on 127.0.0.1:8765. This lets containers reach
the host without binding the MCP service to 0.0.0.0 (which would expose it on
the public interface).
"""

import asyncio
import os
import signal
import sys

BIND_HOSTS = [
    item.strip()
    for item in os.getenv(
        "KB_MCP_PROXY_BIND_HOSTS",
        "172.17.0.1,172.18.0.1,172.19.0.1,172.20.0.1",
    ).split(",")
    if item.strip()
]
PORT = int(os.getenv("KB_MCP_PROXY_PORT", "8765").strip() or "8765")
TARGET_HOST = os.getenv("KB_MCP_PROXY_TARGET_HOST", "127.0.0.1").strip()
TARGET_PORT = int(os.getenv("KB_MCP_PROXY_TARGET_PORT", "8765").strip() or "8765")


async def relay(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.write_eof()
        except (OSError, RuntimeError):
            pass


async def handle(client_reader, client_writer):
    try:
        server_reader, server_writer = await asyncio.open_connection(
            TARGET_HOST, TARGET_PORT
        )
    except OSError as exc:
        print(f"target unavailable: {exc}", file=sys.stderr, flush=True)
        client_writer.close()
        return
    try:
        await asyncio.gather(
            relay(client_reader, server_writer),
            relay(server_reader, client_writer),
        )
    finally:
        for writer in (client_writer, server_writer):
            try:
                writer.close()
            except Exception:
                pass


async def main():
    servers = []
    for host in BIND_HOSTS:
        try:
            servers.append(
                await asyncio.start_server(handle, host=host, port=PORT, reuse_address=True)
            )
            print(f"forwarding {host}:{PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
        except OSError as exc:
            print(f"skip {host}:{PORT}: {exc}", file=sys.stderr, flush=True)
    if not servers:
        raise SystemExit("no gateway addresses could be bound")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for server in servers:
        server.close()
    await asyncio.gather(*(server.wait_closed() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
