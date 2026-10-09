"""Builds the edit workload of bench_serve_vista.sbatch from Lean files that
AIProver sessions wrote (aiprover/work/jobs/*/s*/proj/Work.lean).

Each prompt asks for the complete file back with a small change, as the
harness's edit and write_file turns do, so most output tokens repeat the
context. Random-token prompts cannot exercise prompt-lookup speculation; this
workload measures its acceptance on representative text.

    python3 scripts/vista/make_edit_dataset.py --out temp/edit_dataset.jsonl

The output is a `vllm bench serve --dataset-name custom` JSONL file
({"prompt", "output_tokens"} per line).
"""

import argparse
import json
import random
from pathlib import Path

INSTRUCTION = (
    "Return the complete Lean 4 file below, unchanged except that every "
    "theorem and lemma receives a one-line docstring stating its content. "
    "Output only the file in one ```lean block.\n\n```lean\n{source}\n```"
)
# Tekken averages about 3.2 characters per token on Lean source; the reply
# repeats the file and adds docstrings.
CHARS_PER_TOKEN = 3.2
REPLY_GROWTH = 1.3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=Path,
                        default=Path("aiprover/work/jobs"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--min-bytes", type=int, default=3000)
    parser.add_argument("--max-bytes", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    sources = {}
    for path in sorted(args.jobs.glob("*/s*/proj/Work.lean")):
        text = path.read_text(errors="replace")
        if args.min_bytes <= len(text) <= args.max_bytes:
            sources.setdefault(text, path)
    texts = sorted(sources)
    random.Random(args.seed).shuffle(texts)
    texts = texts[:args.count]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        for text in texts:
            reply = int(len(text) / CHARS_PER_TOKEN * REPLY_GROWTH)
            record = {"prompt": INSTRUCTION.format(source=text),
                      "output_tokens": reply}
            f.write(json.dumps(record) + "\n")
    print(f"{len(texts)} prompts from {len(sources)} distinct files "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
