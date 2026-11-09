"""Model servers added on the page (server/model_servers.py)."""

from tests.support import ServerProcessTest

FLOW = """
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from fastapi.testclient import TestClient
from aiprover_orchestration.server import models
from aiprover_orchestration.server.app import CONFIG, app, in_data


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "m"}]}).encode())

    def log_message(self, *_):
        pass


stub = HTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{stub.server_port}/v1"
client = TestClient(app, client=("127.0.0.1", 5))
add = {"name": "mine", "url": url, "api_key": "secret"}
replies = [
    client.post("/api/model-servers", json=add).json(),
    client.post("/api/model-servers", json=add | {"model": "x"}).status_code,
    TestClient(app, client=("8.8.8.8", 5)).post(
        "/api/model-servers", json=add
    ).status_code,
]
listed = client.get("/api/models?page=dev").text
choice = {"provider": "custom", "model": "mine"}
selection = models.validate({"orchestrator": choice, "subagent": choice})
config = models.build_config(CONFIG, selection)
print(json.dumps([replies, "secret" in listed, config["agents"]["prover"],
                  "secret" in json.dumps(config)]))
"""


class ModelServerTest(ServerProcessTest):
    def test_a_server_is_checked_and_its_key_stays_private(self):
        replies, listed, prover, in_config = self.run_python(FLOW)
        self.assertEqual(replies[0], {"name": "mine", "model": "m"})
        self.assertEqual(replies[1:], [400, 403])
        self.assertFalse(listed or in_config)
        self.assertEqual(prover["backend"], "openai_compatible")
        self.assertTrue(prover["api_key_file"].endswith("mine.key"))
