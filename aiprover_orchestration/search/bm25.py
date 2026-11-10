"""Okapi BM25 keyword scores over a fixed list of documents, in numpy alone.

The index holds, for each term, the documents that contain it and their
term weights; a query adds the weights of its terms. Robertson and Zaragoza,
"The Probabilistic Relevance Framework: BM25 and Beyond" (2009).
"""

import json
import re
import unicodedata
from pathlib import Path

import numpy as np

K1 = 1.2
B = 0.75
# Grammatical words carry no topic and hold the longest posting lists.
STOPWORDS = set(
    "a an and are as at be by for from has have in is it its of on or that the "
    "their these this to was we which with".split()
)
# LaTeX accents, as in Erd\H{o}s or G\"odel, written as the plain letter.
LATEX_ACCENT = re.compile(r"\\[\"'`^~=.uvHckrbdt]\s*\{?([A-Za-z])\}?")


def tokens(text: str) -> list[str]:
    text = LATEX_ACCENT.sub(r"\1", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return [word for word in re.findall(r"[a-z0-9]+", text.lower())
            if word not in STOPWORDS]  # fmt: skip


def build(texts: list[str], folder: Path) -> None:
    """Write the index of `texts` to `folder`: bm25_vocabulary.json, and the
    postings (bm25_documents.npy, bm25_weights.npy) cut by bm25_offsets.npy."""
    vocabulary: dict[str, int] = {}
    documents, terms, counts, lengths = [], [], [], []
    for index, text in enumerate(texts):
        words = tokens(text)
        lengths.append(len(words))
        ids = np.array([vocabulary.setdefault(w, len(vocabulary)) for w in words],
                       dtype=np.int32)  # fmt: skip
        unique, count = np.unique(ids, return_counts=True)
        documents.append(np.full(len(unique), index, dtype=np.int32))
        terms.append(unique)
        counts.append(count.astype(np.float32))
    documents, terms, counts = map(np.concatenate, (documents, terms, counts))
    lengths = np.array(lengths, dtype=np.float32)
    order = np.argsort(terms, kind="stable")
    documents, terms, counts = documents[order], terms[order], counts[order]
    frequency = np.bincount(terms, minlength=len(vocabulary))
    idf = np.log1p((len(texts) - frequency + 0.5) / (frequency + 0.5))
    norm = K1 * (1 - B + B * lengths[documents] / lengths.mean())
    weights = idf[terms] * counts * (K1 + 1) / (counts + norm)
    offsets = np.concatenate([[0], np.cumsum(frequency)]).astype(np.int64)
    np.save(folder / "bm25_documents.npy", documents)
    np.save(folder / "bm25_weights.npy", weights.astype(np.float32))
    np.save(folder / "bm25_offsets.npy", offsets)
    (folder / "bm25_vocabulary.json").write_text(json.dumps(vocabulary))


class Keywords:
    def __init__(self, folder: Path, size: int):
        self.vocabulary = json.loads((folder / "bm25_vocabulary.json").read_text())
        self.documents = np.load(folder / "bm25_documents.npy")
        self.weights = np.load(folder / "bm25_weights.npy")
        self.offsets = np.load(folder / "bm25_offsets.npy")
        self.size = size

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(self.size, dtype=np.float32)
        for word in set(tokens(query)):
            if word in self.vocabulary:
                term = self.vocabulary[word]
                start, end = self.offsets[term], self.offsets[term + 1]
                # A term lists each document once, so the indices are unique.
                scores[self.documents[start:end]] += self.weights[start:end]
        return scores
