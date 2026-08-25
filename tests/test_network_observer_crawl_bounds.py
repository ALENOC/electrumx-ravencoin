# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression test: probe_endpoint()'s real open_connection() path did not
pass `limit=` to asyncio, so the StreamReader silently enforced asyncio's
unrelated 64 KiB default instead of the configured Limits.max_response_bytes
declared in network_observer/model.py. A legitimate response between 64 KiB and the
configured budget was misclassified as RPC_MALFORMED ("chunk is longer than
limit") even though it was well within the operator's configured policy -
the size bound in network_observer/model.py was dead configuration.
"""

import asyncio
import json

from network_observer.crawl import probe_endpoint
from network_observer.model import EndpointId, Limits, Transport


class _FakeWriter:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass

    def get_extra_info(self, name):
        return None


def endpoint(host="127.0.0.1", port=50002):
    return EndpointId(host, port, Transport.TCP)


def _connector_with_oversized_line(limit):
    async def connector(target, context):
        reader = asyncio.StreamReader(limit=limit)
        reader.feed_data(b"x" * (limit * 4))
        reader.feed_eof()
        return reader, _FakeWriter()
    return connector


def test_oversized_response_is_contained_not_raised():
    connector = _connector_with_oversized_line(64)
    result = asyncio.run(probe_endpoint(
        endpoint(), connector=connector, allow_private=True,
        limits=Limits(max_response_bytes=64)))

    assert result.reachable is False
    assert result.error_category == "RPC_MALFORMED"


def test_real_connection_honors_configured_max_response_bytes():
    # A response between 64 KiB and the operator's configured
    # max_response_bytes must be accepted: the configured policy is what
    # governs peer probing, not asyncio's unrelated 64 KiB StreamReader
    # default. Padding is well within the 200_000-byte budget below but
    # above the 64 KiB default that used to silently take precedence.
    padding = "a" * 100_000

    async def scenario():
        async def handle(reader, writer):
            while True:
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line)
                if request["method"] == "server.version":
                    result = ["ElectrumX-RVN", "1.4", padding]
                else:
                    result = {}
                writer.write((json.dumps(
                    {"id": request["id"], "result": result}) + "\n").encode())
                await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            limits = Limits(max_response_bytes=200_000, tcp_timeout=2,
                            rpc_timeout=2)
            return await probe_endpoint(
                EndpointId("127.0.0.1", port, Transport.TCP),
                allow_private=True, limits=limits)
        finally:
            server.close()
            await server.wait_closed()

    result = asyncio.run(scenario())
    assert result.reachable is True
    assert result.error is None
