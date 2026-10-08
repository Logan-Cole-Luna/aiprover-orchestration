"""Command-line access to the library; every command prints JSON.

    python -m aiprover_orchestration add-definition NAME FILE
    python -m aiprover_orchestration add-theorem NAME FILE [--title ...]
    python -m aiprover_orchestration verify THEOREM FILE [--disprove]
    python -m aiprover_orchestration show NAME
    python -m aiprover_orchestration search [--query Q] [--status S]
    python -m aiprover_orchestration graph THEOREM
    python -m aiprover_orchestration open-leaves THEOREM
"""

import argparse
import json
from pathlib import Path

from ..core.layout import DATABASE
from ..core.library import Library, Rejected
from ..core.store import Store


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aiprover_orchestration",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)
    for kind in ("definition", "theorem"):
        publish = commands.add_parser(f"add-{kind}")
        publish.add_argument("name")
        publish.add_argument("file", type=Path)
        publish.add_argument("--title", default="")
        publish.add_argument("--statement", default="",
                             help="natural-language statement")
        publish.add_argument("--source", default="")
        publish.add_argument("--tags", nargs="*", default=[])
    verify = commands.add_parser("verify")
    verify.add_argument("theorem")
    verify.add_argument("file", type=Path)
    verify.add_argument("--disprove", action="store_true")
    verify.add_argument("--explanation", default="")
    search = commands.add_parser("search")
    search.add_argument("--query", default="")
    search.add_argument("--status", default="")
    search.add_argument("--kind", default="")
    for command in ("show", "graph", "open-leaves"):
        commands.add_parser(command).add_argument("name")
    return parser.parse_args(argv)


def run(arguments: argparse.Namespace, library: Library):
    command = arguments.command
    if command.startswith("add-"):
        return library.publish(command[4:], arguments.name,
                               arguments.file.read_text(),
                               title=arguments.title,
                               statement_nl=arguments.statement,
                               source=arguments.source, tags=arguments.tags)
    if command == "verify":
        return library.verify(arguments.theorem, arguments.file.read_text(),
                              "disprove" if arguments.disprove else "prove",
                              arguments.explanation)
    if command == "search":
        return library.store.search(arguments.query, arguments.status,
                                    arguments.kind)
    if command == "show":
        return library.show(arguments.name)
    theorem_id = library.theorem_id(arguments.name)
    if command == "graph":
        return library.store.graph(theorem_id)
    return library.store.open_leaves(theorem_id)


def main(argv: list[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    library = Library(Store(arguments.db))
    try:
        result = run(arguments, library)
    except Rejected as rejection:
        raise SystemExit(f"rejected: {rejection}")
    print(json.dumps(result, indent=1, ensure_ascii=False))
