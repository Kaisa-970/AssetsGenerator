"""LAN entry point for a model protocol server kept on loopback.

Forwards bytes only, preserving the upstream identity and job semantics.
"""

import argparse
import asyncio


async def forward(reader, writer, target_port):
    upstream = None
    try:
        remote, upstream = await asyncio.open_connection("127.0.0.1", target_port)

        async def pump(source, destination):
            while data := await source.read(65536):
                destination.write(data)
                await destination.drain()
            if destination.can_write_eof():
                destination.write_eof()

        await asyncio.wait_for(
            asyncio.gather(pump(reader, upstream), pump(remote, writer)), timeout=1800
        )
    finally:
        writer.close()
        if upstream is not None:
            upstream.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--target-port", required=True, type=int)
    args = parser.parse_args()
    server = await asyncio.start_server(
        lambda r, w: forward(r, w, args.target_port), args.host, args.port
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
