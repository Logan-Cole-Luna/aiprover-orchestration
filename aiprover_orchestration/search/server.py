"""Semantic search over the pinned Mathlib, with the Lean Finder protocol.

    python -m aiprover_orchestration.search.server \\
        --index data/mathlib_index --port 18570

POST / with {"inputs": query, "top_k": k} returns {"results": [...]}, each
result with formal_name, informal_name, kind, type, informal_description
and path, as lean_lsp_mcp's `lean_leanfinder` tool expects (`version` is
ignored: the index is of the pinned Mathlib). Results are ranked by cosine
similarity of query and document embeddings; declarations whose name or
last name component the query states exactly (a token that reads as a
Lean name, not a word) come first.
"""

import argparse
import asyncio
import json
import logging
import re
import threading
from pathlib import Path

import numpy as np
import torch
from aiohttp import web

from .embed import Embedder, query_text

MAX_RESULTS = 50
FIELDS = (
    "formal_name",
    "informal_name",
    "kind",
    "type",
    "informal_description",
    "path",
)
NAME_TOKEN = re.compile(r"[A-Za-z_][\w'.]*")
# A token that reads as a Lean name rather than a word: qualified, snake
# case, or with an inner capital (`Finset.sum_le_sum`, `IsLocalRing`).
LEAN_NAME = re.compile(r"\w[._]\w|[a-z][A-Z]")

logger = logging.getLogger(__name__)


class Index:
    def __init__(self, directory: Path):
        with open(directory / "documents.jsonl") as f:
            self.documents = [
                {key: entry[key] for key in FIELDS}
                for entry in map(json.loads, f)
            ]
        self.vectors = np.load(directory / "embeddings.npy").astype(np.float32)
        self.by_name: dict[str, list[int]] = {}
        for i, document in enumerate(self.documents):
            name = document["formal_name"]
            for key in {name, name.rsplit(".", 1)[-1]}:
                self.by_name.setdefault(key, []).append(i)
        self.embedder = Embedder(str(directory / "qwen3-embedding-0.6b"))
        self.lock = threading.Lock()  # one query through the model at a time
        logger.info(f"index of {len(self.documents)} declarations loaded")

    def search(self, query: str, top_k: int) -> list[dict]:
        with self.lock:
            vector = self.embedder([query_text(query)])[0]
        scores = self.vectors @ vector
        exact = [
            i
            for token in dict.fromkeys(NAME_TOKEN.findall(query))
            if LEAN_NAME.search(token)
            for i in self.by_name.get(token, [])
        ]
        ranked = list(dict.fromkeys(exact))
        for i in np.argsort(-scores)[: top_k + len(ranked)]:
            if int(i) not in ranked:
                ranked.append(int(i))
        return [self.documents[i] for i in ranked[:top_k]]


async def handle_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        query = str(body["inputs"]).strip()
        top_k = max(1, min(int(body.get("top_k", 5)), MAX_RESULTS))
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "expected inputs and top_k"})
    if not query:
        return web.json_response({"results": []})
    index: Index = request.app["index"]
    request.app["queries"][0] += 1
    results = await asyncio.to_thread(index.search, query, top_k)
    return web.json_response({"results": results})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "declarations": len(request.app["index"].documents),
            "queries": request.app["queries"][0],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--index", type=Path, default=Path("data/mathlib_index")
    )
    parser.add_argument("--port", type=int, default=18570)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    torch.set_num_threads(args.threads)
    app = web.Application()
    app["index"] = Index(args.index)
    app["queries"] = [0]  # served since start
    app.router.add_post("/", handle_search)
    app.router.add_get("/health", handle_health)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
