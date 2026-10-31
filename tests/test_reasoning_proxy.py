"""Recording of a reply whose client closes the connection."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import ClientError, web
from aiohttp.test_utils import TestClient, TestServer

from aiprover_orchestration.server import reasoning_proxy


class Upstream:
    """A client whose every request receives one streamed chunk."""

    async def request(self, *_, **__):
        chunk = {
            "choices": [
                {"delta": {"content": "Answer"}, "finish_reason": "length"}
            ]
        }

        async def chunks():
            yield f"data: {json.dumps(chunk)}\n\n".encode()

        return SimpleNamespace(
            status=200,
            headers={"content-type": "text/event-stream"},
            content=SimpleNamespace(iter_any=chunks),
            release=lambda: None,
        )


async def closed(*_):
    raise ConnectionResetError


class ClosedClientTest(unittest.IsolatedAsyncioTestCase):
    async def recorded_reply(self, directory: Path, closes_at: str):
        app = web.Application()
        app["client"] = Upstream()
        app["upstreams"] = reasoning_proxy.Upstreams(["http://upstream"])
        app["in_flight"] = [0]
        app["model"] = ""
        app["max_reply_tokens"] = 1
        app.router.add_route("*", "/{tail:.*}", reasoning_proxy.forward)
        client = TestClient(TestServer(app))
        await client.start_server()
        fit = mock.AsyncMock(return_value=(False, 1))
        try:
            with mock.patch.object(
                reasoning_proxy, "session_of", return_value=(directory, 1)
            ), mock.patch.object(
                reasoning_proxy, "fit_context", fit
            ), mock.patch.object(web.StreamResponse, closes_at, closed):
                await client.post("/v1/chat/completions", data=b"{}")
        except ClientError:
            pass
        finally:
            await client.close()
        record = directory / "reasoning.jsonl"
        return json.loads(record.read_text()) if record.exists() else None

    async def test_chunk_in_hand_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = await self.recorded_reply(Path(directory), "write")
        self.assertEqual(entry["content_chars"], len("Answer"))
        self.assertTrue(entry["truncated"])
        self.assertTrue(entry["client_closed"])

    async def test_close_before_the_reply_starts_is_handled(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertNoLogs("aiohttp.server", "ERROR"):
                entry = await self.recorded_reply(Path(directory), "prepare")
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
