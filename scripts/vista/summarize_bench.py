"""Tabulates a bench_serve_vista.sbatch result directory.

    python3 scripts/vista/summarize_bench.py <bench_dir> [--reference A]

Per arm and load: per-request decode speed (1000 / mean TPOT), aggregate
output throughput, median and p90 inter-token latency, mean TTFT. For the
edit load: the share of greedy completions identical to the reference arm
and their mean common-prefix fraction (CUDA graphs and kernel changes alter
floating-point reduction order, so long greedy texts may diverge late), and
the speculative acceptance from the arm's /metrics counters.
"""

import argparse
import json
import os
import re
from pathlib import Path

LOADS = ("c1_8k", "c1_80k", "c13_80k", "edit_c8")


def common_prefix_fraction(a: str, b: str) -> float:
    shared = len(os.path.commonprefix([a, b]))
    return shared / max(len(a), len(b), 1)


def spec_acceptance(metrics_file: Path) -> str:
    if not metrics_file.exists():
        return ""
    counters = {}
    for line in metrics_file.read_text().splitlines():
        match = re.match(r"(vllm:spec_decode_num_\w+?)(_total)?(\{.*\})? ([\d.e+]+)$",
                         line)
        if match:
            name = match.group(1)
            counters[name] = counters.get(name, 0.0) + float(match.group(4))
    drafts = counters.get("vllm:spec_decode_num_drafts", 0)
    drafted = counters.get("vllm:spec_decode_num_draft_tokens", 0)
    accepted = counters.get("vllm:spec_decode_num_accepted_tokens", 0)
    if not drafted:
        return ""
    return (f"{accepted / drafted:.0%} of drafts, "
            f"{1 + accepted / drafts:.2f} tokens/step")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_dir", type=Path)
    parser.add_argument("--reference", default="A")
    args = parser.parse_args()

    results = {}
    for path in args.bench_dir.glob("*_*.json"):
        arm, load = path.stem.split("_", 1)
        results[arm, load] = json.loads(path.read_text())
    arms = sorted({arm for arm, _ in results})

    print("| Arm | Load | tok/s per request | tok/s aggregate | ITL p50 / p90 ms "
          "| TTFT ms | Completed |")
    print("|---|---|---|---|---|---|---|")
    for arm in arms:
        for load in LOADS:
            r = results.get((arm, load))
            if r is None:
                continue
            per_request = 1000 / r["mean_tpot_ms"] if r.get("mean_tpot_ms") else 0
            print(f"| {arm} | {load} | {per_request:.1f} "
                  f"| {r['output_throughput']:.0f} "
                  f"| {r.get('median_itl_ms', 0):.1f} / {r.get('p90_itl_ms', 0):.1f} "
                  f"| {r.get('mean_ttft_ms', 0):.0f} | {r['completed']} |")

    reference = results.get((args.reference, "edit_c8"), {}).get("generated_texts")
    print(f"\n| Arm | Greedy texts identical to {args.reference} "
          "| Mean common prefix | Speculative acceptance |")
    print("|---|---|---|---|")
    for arm in arms:
        texts = results.get((arm, "edit_c8"), {}).get("generated_texts")
        identical = prefix = ""
        if texts and reference and len(texts) == len(reference):
            pairs = list(zip(texts, reference))
            identical = f"{sum(a == b for a, b in pairs)}/{len(pairs)}"
            mean = sum(common_prefix_fraction(a, b) for a, b in pairs) / len(pairs)
            prefix = f"{mean:.0%}"
        acceptance = spec_acceptance(args.bench_dir / f"{arm}_metrics.txt")
        print(f"| {arm} | {identical} | {prefix} | {acceptance} |")


if __name__ == "__main__":
    main()
