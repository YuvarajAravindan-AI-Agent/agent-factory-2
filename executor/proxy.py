"""Tiny CONNECT allow-list proxy for gate stage 1 (stdlib only, runs in python:3.12-slim)."""

import asyncio
import os

ALLOW = {
    h.strip().lower()
    for h in os.environ.get("AF_ALLOW", "pypi.org,files.pythonhosted.org").split(",")
}


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except Exception:
        pass
    finally:
        w.close()


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    try:
        line = (await cr.readline()).decode(errors="replace").split()
        while (await cr.readline()) not in (b"\r\n", b"\n", b""):
            pass
        if len(line) < 2 or line[0] != "CONNECT":
            cw.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, _, port = line[1].partition(":")
        if host.lower() not in ALLOW or port not in ("443", ""):
            print("DENY", line[1], flush=True)
            cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        ur, uw = await asyncio.open_connection(host, int(port or 443))
        cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
    except Exception as e:
        print("ERR", e, flush=True)
    finally:
        cw.close()


async def main() -> None:
    srv = await asyncio.start_server(handle, "0.0.0.0", 3128)  # noqa: S104
    async with srv:
        await srv.serve_forever()


asyncio.run(main())
