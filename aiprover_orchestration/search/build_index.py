"""Index documents: the pinned Mathlib's declarations with informal text.

    python -m aiprover_orchestration.search.build_index \\
        data/mathlib_index/declarations.jsonl \\
        data/mathlib_index/lsv2-mathlib-v4.28.0-rc1.jsonl \\
        data/mathlib_index/documents.jsonl

A declaration of the pinned Mathlib takes its informal name, description
and type from the LeanSearch v2 corpus entry of the same name; one absent
there (renamed or removed by v4.28) keeps its printed type and docstring.
Each document's `text` is what gets embedded.
"""

import json
import re
import sys

KINDS = {"theorem", "definition", "inductive", "axiom", "opaque"}
# Generated declarations (equation lemmas, matchers, no-confusion types),
# notation, and metaprogramming.
GENERATED = re.compile(
    r"\.(?:eq_\d+|eq_def|match_\d+|proof_\d+|_\w+|sizeOf_spec|injEq|ext_iff"
    r"|noConfusion\w*|rec\w*|below|brecOn|binductionOn|casesOn|ibelow)$"
    r"|\.noConfusionType\.|^«|^(?:Mathlib\.(?:Tactic|Meta|Linter|Util)"
    r"|Lean|ToAdditive|Aesop|Batteries\.Tactic)\."
)


def corpus_entries(path: str) -> dict[str, dict]:
    entries = {}
    with open(path) as f:
        for line in f:
            entry = json.loads(line)
            entries[".".join(entry["name"])] = entry
    return entries


def document(declaration: dict, entry: dict | None) -> dict:
    informal_name = (entry or {}).get("informal_name", "")
    description = (entry or {}).get("informal_description", "")
    statement = (entry or {}).get("type") or declaration["type"]
    doc = declaration["doc"]
    text = "\n".join(
        part
        for part in (
            f"{informal_name}: {description}" if informal_name else "",
            doc if not description else "",
            f"{declaration['name']} : {statement}",
        )
        if part
    )
    return {
        "formal_name": declaration["name"],
        "informal_name": informal_name,
        "kind": declaration["kind"],
        "type": statement,
        "informal_description": description or doc,
        "path": declaration["module"],
        "text": text[:2000],
    }


def main() -> None:
    declarations, corpus, output = sys.argv[1:4]
    entries = corpus_entries(corpus)
    kept = described = 0
    with open(declarations) as f, open(output, "w") as out:
        for line in f:
            declaration = json.loads(line)
            if declaration["kind"] not in KINDS or GENERATED.search(
                declaration["name"]
            ):
                continue
            entry = entries.get(declaration["name"])
            out.write(json.dumps(document(declaration, entry)) + "\n")
            kept += 1
            described += entry is not None
    print(f"{kept} documents, {described} with informal descriptions")


if __name__ == "__main__":
    main()
