"""Embeddings of index documents or queries with Qwen3-Embedding.

    python embed.py MODEL_DIR documents.jsonl embeddings.npy

Documents are embedded as they are, queries with the retrieval instruction
(`query_text`); vectors are last-token pooled and L2-normalized. Depends on
torch, transformers and numpy only, so that it runs in the serving container
on a GPU node.
"""

import json
import sys

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

INSTRUCTION = (
    "Given a mathematical statement, concept or proof goal, retrieve the "
    "Lean 4 Mathlib declarations that state or define it"
)
MAX_TOKENS = 512


def query_text(query: str) -> str:
    return f"Instruct: {INSTRUCTION}\nQuery:{query}"


class Embedder:
    def __init__(self, model_dir: str, device: str = "cpu"):
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_dir, padding_side="left"
        )
        dtype = torch.float16 if device != "cpu" else torch.float32
        self.model = AutoModel.from_pretrained(model_dir, dtype=dtype)
        self.model.to(device).eval()
        self.device = device

    @torch.inference_mode()
    def __call__(self, texts: list[str]) -> np.ndarray:
        batch = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=MAX_TOKENS,
            return_tensors="pt",
        ).to(self.device)
        hidden = self.model(**batch).last_hidden_state[:, -1]
        hidden = torch.nn.functional.normalize(hidden.float(), dim=-1)
        return hidden.cpu().numpy()


def main() -> None:
    model_dir, documents, output = sys.argv[1:4]
    with open(documents) as f:
        texts = [json.loads(line)["text"] for line in f]
    embedder = Embedder(model_dir, "cuda")
    # Sorted by length, so that each batch pads little.
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    vectors = np.zeros((len(texts), 1024), dtype=np.float16)
    batch = 256
    for start in range(0, len(order), batch):
        chunk = order[start : start + batch]
        vectors[chunk] = embedder([texts[i] for i in chunk])
        if start % (batch * 100) == 0:
            print(f"{start}/{len(texts)}", flush=True)
    np.save(output, vectors)
    print(f"saved {vectors.shape} to {output}")


if __name__ == "__main__":
    main()
