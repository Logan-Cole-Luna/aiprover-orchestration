"""Semantic search over arXiv math abstracts, and the text of papers, for the
`paper-search` command (search/paper_search.py).

    python -m aiprover_orchestration.search.papers \\
        --index data/arxiv_index --port 18571

POST /search with {"query", "top_k", "before"} ranks the papers first
submitted before `before` (YYYY-MM-DD; empty: every paper) by fusing two
rankings of their title and abstract: cosine similarity of embeddings, and
BM25 keyword scores (search/bm25.py). GET /read/<id>?page=n
&before= returns one page of a paper's text, fetched once from arXiv (its HTML,
else its PDF through pdftotext) and cached in the index's papers/ folder.

This is the only component that reaches the network: only arXiv, only for
identifiers of arXiv's formats, at most one fetch every three seconds. It
listens on the loopback interface, on a port that offline AIProver sessions
cannot reach (lean/offline.py).
"""

import argparse
import asyncio
import json
import logging
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import torch
from aiohttp import web

from .arxiv_index import AGENT, MODEL_DIR, REQUEST_INTERVAL
from .bm25 import Keywords
from .embed import Embedder

MAX_RESULTS = 20
# Candidates from each ranking, and the constant of reciprocal rank fusion
# (Cormack, Clarke, and Buettcher, SIGIR 2009).
CANDIDATES = 100
FUSION_K = 60
PAGE_CHARS = 20_000
MAX_BYTES = 20 * 2**20
# A shorter HTML text is arXiv's notice that the paper has no HTML version.
MIN_HTML_CHARS = 5_000
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")
SKIPPED_TAGS = {"script", "style", "nav", "header", "footer"}

logger = logging.getLogger(__name__)


class Text(HTMLParser):
    """The text of the article body of an arXiv HTML paper: formulas as their
    LaTeX source, table cells separated by " | ", and images as [image]."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0
        self.in_article = False

    def handle_starttag(self, tag, attributes):
        self.in_article |= tag == "article"
        shown = self.in_article and not self.skipping
        if tag == "math" and shown:
            self.parts.append(f" ${dict(attributes).get('alttext', '')}$ ")
        if tag == "math" or tag in SKIPPED_TAGS:
            self.skipping += 1
        elif not shown:
            pass
        elif tag == "img":
            self.parts.append(" [image] ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in {"p", "div", "section", "h1", "h2", "h3", "h4", "li", "tr", "figcaption"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "math" or tag in SKIPPED_TAGS:
            self.skipping = max(self.skipping - 1, 0)
        self.in_article &= tag != "article"

    def handle_data(self, data):
        # Line breaks between tags in the source are layout, not text.
        if self.in_article and not self.skipping:
            self.parts.append(data if data.strip() else " ")


class Papers:
    def __init__(self, folder: Path):
        with open(folder / "documents.jsonl") as f:
            self.documents = [json.loads(line) for line in f]
        self.vectors = np.load(folder / "embeddings.npy").astype(np.float32)
        self.dates = np.array([paper["date"] for paper in self.documents])
        self.by_id = {paper["id"]: paper for paper in self.documents}
        self.embedder = Embedder(str(folder / MODEL_DIR), pooling="cls")
        self.keywords = Keywords(folder, len(self.documents))
        self.cache = folder / "papers"
        self.cache.mkdir(exist_ok=True)
        self.lock = threading.Lock()  # one query through the model at a time
        self.fetching = threading.Lock()  # one fetch from arXiv at a time
        self.last_fetch = 0.0
        logger.info(f"index of {len(self.documents)} papers loaded")

    def search(self, query: str, top_k: int, before: str) -> list[dict]:
        with self.lock:
            vector = self.embedder([QUERY_PREFIX + query])[0]
        keywords = self.keywords.scores(query)
        keywords[keywords == 0] = -np.inf  # no query term in the paper
        count = min(CANDIDATES, len(self.documents))
        fused: dict[int, float] = {}
        for scores in (self.vectors @ vector, keywords):
            if before:
                scores[self.dates >= before] = -np.inf
            best = np.argpartition(-scores, count - 1)[:count]
            best = best[np.argsort(-scores[best])]
            for rank, i in enumerate(i for i in best if np.isfinite(scores[i])):
                fused[i] = fused.get(i, 0) + 1 / (FUSION_K + rank + 1)
        best = sorted(fused, key=fused.get, reverse=True)[:top_k]
        return [self.documents[i] for i in best]

    def download(self, url: str) -> bytes:
        """At most MAX_BYTES of `url`, keeping arXiv's request interval."""
        time.sleep(max(0.0, self.last_fetch + REQUEST_INTERVAL - time.time()))
        self.last_fetch = time.time()
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=120) as reply:
            return reply.read(MAX_BYTES)

    def text(self, paper_id: str) -> str:
        path = self.cache / f"{paper_id.replace('/', '_')}.txt"
        with self.fetching:
            if path.exists():
                return path.read_text()
            parser = Text()
            try:
                parser.feed(self.download(f"https://arxiv.org/html/{paper_id}").decode())
            except OSError:
                pass
            text = re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()
            if len(text) < MIN_HTML_CHARS:
                with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
                    pdf.write(self.download(f"https://export.arxiv.org/pdf/{paper_id}"))
                    pdf.flush()
                    text = subprocess.run(
                        ["pdftotext", pdf.name, "-"],
                        capture_output=True, text=True, timeout=300,
                    ).stdout  # fmt: skip
            path.write_text(text)
            return text

    def read(self, paper_id: str, page: int, before: str) -> dict:
        if not ARXIV_ID.fullmatch(paper_id):
            return {"error": f"{paper_id!r} is not an arXiv identifier"}
        paper = self.by_id.get(re.sub(r"v\d+$", "", paper_id), {})
        if before and not paper.get("date", "9999") < before:
            return {"error": f"{paper_id} is not available before {before}"}
        try:
            text = self.text(paper_id)
        except OSError as error:
            return {"error": f"{paper_id} could not be fetched: {error}"}
        pages = max(1, -(-len(text) // PAGE_CHARS))
        page = min(max(page, 1), pages)
        return {
            "id": paper_id,
            "title": paper.get("title", ""),
            "date": paper.get("date", ""),
            "page": page,
            "pages": pages,
            "text": text[(page - 1) * PAGE_CHARS : page * PAGE_CHARS],
        }


async def handle_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        query = str(body["query"]).strip()
        top_k = max(1, min(int(body.get("top_k", 10)), MAX_RESULTS))
        before = str(body.get("before") or "")
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "expected query, top_k and before"})
    request.app["queries"][0] += 1
    papers: Papers = request.app["papers"]
    found = await asyncio.to_thread(papers.search, query, top_k, before)
    return web.json_response({"results": found})


async def handle_read(request: web.Request) -> web.Response:
    try:
        page = int(request.query.get("page", 1))
    except ValueError:
        return web.json_response({"error": "page must be a number"})
    request.app["queries"][0] += 1
    papers: Papers = request.app["papers"]
    reply = await asyncio.to_thread(
        papers.read, request.match_info["id"], page, request.query.get("before", "")
    )
    return web.json_response(reply)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "papers": len(request.app["papers"].documents),
            "queries": request.app["queries"][0],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", type=Path, default=Path("data/arxiv_index"))
    parser.add_argument("--port", type=int, default=18571)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    torch.set_num_threads(args.threads)
    app = web.Application()
    app["papers"] = Papers(args.index)
    app["queries"] = [0]  # served since start
    app.router.add_post("/search", handle_search)
    app.router.add_get(r"/read/{id:.+}", handle_read)
    app.router.add_get("/health", handle_health)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
