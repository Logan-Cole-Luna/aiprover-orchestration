"""The index of arXiv math abstracts that `paper-search` searches
(search/papers.py), from arXiv's public metadata (CC0) over OAI-PMH.

    python -m aiprover_orchestration.search.arxiv_index model data/arxiv_index
    python -m aiprover_orchestration.search.arxiv_index harvest data/arxiv_index
    python -m aiprover_orchestration.search.arxiv_index build data/arxiv_index

`model` saves the embedding model. `harvest` appends the records of the math
set to records.jsonl one month of changes at a time, and keeps the last month
completed in harvested.txt, so an interrupted harvest resumes there and a
monthly refresh asks only for the latest changes. `build` writes
documents.jsonl (one record per paper, the latest), embeddings.npy (title
and abstract, one row per document), and the BM25 keyword index (bm25_*),
embedding only papers that are new or changed since the previous build.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import date, timedelta
from pathlib import Path

import numpy as np

OAI = "https://oaipmh.arxiv.org/oai"
SPACE = {"oai": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}
# arXiv asks harvesters for at most one request every three seconds.
REQUEST_INTERVAL = 3.0
AGENT = "aiprover-orchestration arxiv-index"
MODEL = "BAAI/bge-small-en-v1.5"
MODEL_DIR = "bge-small-en-v1.5"
# The earliest change date of arXiv's OAI-PMH records.
FIRST_DAY = date(2007, 5, 1)
ATTEMPTS = 10
BATCH = 128
THREADS = 16


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def records(page: bytes) -> tuple[list[dict], str]:
    """The papers of one ListRecords response, and its resumption token
    (empty after the last)."""
    root = ElementTree.fromstring(page)
    found = []
    for paper in root.iterfind(".//oai:metadata/a:arXiv", SPACE):
        authors = [
            clean(f"{a.findtext('a:forenames', '', SPACE)} {a.findtext('a:keyname', '', SPACE)}")
            for a in paper.iterfind("a:authors/a:author", SPACE)
        ]
        found.append({
            "id": paper.findtext("a:id", "", SPACE),
            "date": paper.findtext("a:created", "", SPACE),
            "title": clean(paper.findtext("a:title", "", SPACE)),
            "authors": ", ".join(authors),
            "categories": paper.findtext("a:categories", "", SPACE),
            "abstract": clean(paper.findtext("a:abstract", "", SPACE)),
        })  # fmt: skip
    token = root.find(".//oai:resumptionToken", SPACE)
    return found, (token.text or "").strip() if token is not None else ""


def fetch(query: dict) -> bytes:
    request = urllib.request.Request(
        f"{OAI}?{urllib.parse.urlencode(query)}", headers={"User-Agent": AGENT}
    )
    for _ in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=300) as reply:
                return reply.read()
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503):
                raise
            time.sleep(int(error.headers.get("Retry-After") or 60))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(60)
    raise RuntimeError(f"arXiv did not answer {ATTEMPTS} times in a row")


def months(first: date, last: date):
    """(first day, last day) of each calendar month from `first` to `last`."""
    while first <= last:
        following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        yield first, min(following - timedelta(days=1), last)
        first = following


def harvest(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = folder / "harvested.txt"
    first = FIRST_DAY
    if stamp.exists():
        first = date.fromisoformat(stamp.read_text().strip()) + timedelta(days=1)
    count = 0
    with open(folder / "records.jsonl", "a") as out:
        for start, end in months(first, date.today() - timedelta(days=1)):
            query = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": "math",
                     "from": start.isoformat(), "until": end.isoformat()}  # fmt: skip
            while query:
                found, token = records(fetch(query))
                out.writelines(json.dumps(paper) + "\n" for paper in found)
                count += len(found)
                query = token and {"verb": "ListRecords", "resumptionToken": token}
                time.sleep(REQUEST_INTERVAL)
            out.flush()
            stamp.write_text(f"{end.isoformat()}\n")
            print(f"{end}: {count} records", flush=True)


def save_model(folder: Path) -> None:
    from huggingface_hub import snapshot_download

    # The weights in safetensors only; the repository also holds ONNX and pickle copies.
    snapshot_download(MODEL, local_dir=folder / MODEL_DIR,
                      allow_patterns=["*.json", "*.txt", "model.safetensors"])


def text(paper: dict) -> str:
    return f"{paper['title']}. {paper['abstract']}"


def submitted(paper: dict) -> str:
    """The date of the first version. The metadata's `created` is at times the
    date of a later version, whereas the identifier's YYMM is the month of the
    first; when they disagree, the last day of that month is taken, which never
    dates a paper before its first version."""
    number = paper["id"].split("/")[-1]
    year, month = int(number[:2]), int(number[2:4])
    year += 1900 if year > 90 else 2000
    if paper["date"][:7] == f"{year:04d}-{month:02d}":
        return paper["date"]
    following = date(year + month // 12, month % 12 + 1, 1)
    return (following - timedelta(days=1)).isoformat()


def build(folder: Path) -> None:
    """Embed the papers, reusing the vectors of the previous build for papers
    whose title and abstract are unchanged."""
    import torch

    from . import bm25
    from .embed import Embedder

    papers = {}
    with open(folder / "records.jsonl") as f:
        for line in f:
            paper = json.loads(line)
            papers[paper["id"]] = paper | {"date": submitted(paper)}
    documents = [papers[key] for key in sorted(papers)]
    texts = [text(paper) for paper in documents]
    torch.set_num_threads(THREADS)
    embedder = Embedder(str(folder / MODEL_DIR), pooling="cls")
    size = embedder.model.config.hidden_size
    vectors = np.zeros((len(texts), size), dtype=np.float16)
    previous = {}
    if (folder / "embeddings.npy").exists():
        kept = np.load(folder / "embeddings.npy")
        lines = (folder / "documents.jsonl").read_text().splitlines()
        # A build interrupted between its two files leaves them unmatched.
        if len(lines) == len(kept):
            previous = {text(json.loads(line)): row for line, row in zip(lines, kept)}
    new = [i for i, body in enumerate(texts) if body not in previous]
    for i in set(range(len(texts))) - set(new):
        vectors[i] = previous[texts[i]]
    print(f"{len(texts) - len(new)} papers reused, {len(new)} to embed", flush=True)
    new.sort(key=lambda i: len(texts[i]))
    for start in range(0, len(new), BATCH):
        chunk = new[start : start + BATCH]
        vectors[chunk] = embedder([texts[i] for i in chunk])
        if start % (BATCH * 200) == 0:
            print(f"{start}/{len(new)}", flush=True)
    np.save(folder / "embeddings.npy", vectors)
    with open(folder / "documents.jsonl", "w") as out:
        out.writelines(json.dumps(paper) + "\n" for paper in documents)
    bm25.build(texts, folder)
    print(f"saved {len(texts)} papers")


if __name__ == "__main__":
    action, folder = sys.argv[1], Path(sys.argv[2])
    {"model": save_model, "harvest": harvest, "build": build}[action](folder)
