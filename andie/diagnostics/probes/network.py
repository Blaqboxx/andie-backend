"""Probe: network routing and port reachability."""
from __future__ import annotations
import asyncio
import socket

TARGETS = [
    {"name": "blaqtower3-lan",      "host": "192.168.50.9",   "port": 11434},
    {"name": "blaqtower2-lan",      "host": "192.168.50.183", "port": 8010},
    {"name": "blaqtower3-tailscale","host": "100.90.65.67",   "port": 11434},
    {"name": "blaqtower2-tailscale","host": "100.112.224.112","port": 8010},
]


async def _tcp_check(name: str, host: str, port: int) -> dict:
    loop = asyncio.get_event_loop()
    try:
        fut = loop.run_in_executor(None, _tcp_connect, host, port)
        await asyncio.wait_for(fut, timeout=3.0)
        return {"check": name, "status": "healthy", "detail": f"{host}:{port} reachable"}
    except asyncio.TimeoutError:
        return {"check": name, "status": "unreachable", "detail": f"{host}:{port} timeout"}
    except Exception as e:
        return {"check": name, "status": "unreachable", "detail": str(e)[:120]}


def _tcp_connect(host: str, port: int):
    s = socket.create_connection((host, port), timeout=3)
    s.close()


async def run() -> list[dict]:
    return await asyncio.gather(*[_tcp_check(**t) for t in TARGETS])
