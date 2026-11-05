"""The tools of a session on a model without its own: the file tools,
`lean-check`, and sandboxed Python that a Claude session has through its
permission rules. Paths resolve inside the writable tree and the readable trees.

A Codex session reaches them as a Model Context Protocol server on standard
input and output (agents/codex_cli.py), and a chat model directly
(agents/openai_compatible.py).

    python3 -I session_tools.py --write DIR [--read DIR]... [--command NAME]...
        [--max-turns N]

Each `--command` becomes a tool that runs that program from the search path, as
`lean-check` or the session Python, in the writable tree. Beyond `--max-turns`
tool calls, every call fails with TURN_LIMIT.

Run with `python3 -I`, so that nothing in the project is imported in its place.
"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from functools import partial
from pathlib import Path

MAX_REPLY = 100_000
READ_LINES = 2000
COMMAND_SECONDS = 3600
TURN_LIMIT = "The turn limit of this call is reached: reply now."
# Trees a search does not enter: builds hold gigabytes of binary files.
UNSEARCHED = {".lake", ".git", "build"}


def schema(optional: dict[str, str] | None = None, **required: str) -> dict:
    fields = {**required, **(optional or {})}
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in fields.items()},
        "required": list(required),
    }


class Session:
    def __init__(
        self,
        write: str,
        read: list[str],
        commands: list[str],
        max_turns: int = 0,
        execute=partial(subprocess.run, capture_output=True, text=True),
    ):
        self.home = Path(write).resolve()
        self.roots = [self.home, *(Path(path).resolve() for path in read)]
        self.commands = commands
        self.turns_left = max_turns or sys.maxsize
        self.execute = execute

    def resolve(self, name: str, roots: list[Path]) -> Path:
        path = (self.home / name).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise PermissionError(f"{name} is outside this session's files")
        return path

    def allowed(self, path: Path) -> bool:
        """A file inside the session's trees, also through a symbolic link."""
        real = path.resolve()
        return path.is_file() and any(real.is_relative_to(r) for r in self.roots)

    def read_file(self, path: str, offset: int = 1, limit: int = READ_LINES) -> str:
        text = self.resolve(path, self.roots).read_text(errors="replace")
        lines = text.splitlines(keepends=True)
        first = max(int(offset), 1) - 1
        end = first + int(limit)
        if first == 0 and end >= len(lines):
            return text
        note = (
            f"\n[Lines {first + 1} to {min(end, len(lines))} of {len(lines)}."
            + (f" Continue with offset {end + 1}.]" if end < len(lines) else "]")
        )
        return "".join(lines[first:end]) + note

    def list_files(self, pattern: str) -> str:
        names = sorted(
            str(found)
            for root in self.roots
            for found in root.glob(pattern)
            if self.allowed(found)
        )
        return "\n".join(names)

    def searched_files(self, base: Path, include: str):
        """The files under `base`, outside build trees, that match `include`."""
        if base.is_file():
            yield base
        for directory, subdirectories, names in os.walk(base):
            subdirectories[:] = [d for d in subdirectories if d not in UNSEARCHED]
            for name in fnmatch.filter(names, include):
                if self.allowed(Path(directory) / name):
                    yield Path(directory) / name

    def grep(self, pattern: str, path: str, include: str = "*") -> str:
        expression = re.compile(pattern)
        base = self.resolve(path, self.roots)
        return "\n".join(
            f"{file}:{number}:{line}"
            for file in self.searched_files(base, include)
            for number, line in enumerate(
                file.read_text(errors="replace").splitlines(), 1
            )
            if expression.search(line)
        )

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path, [self.home])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"wrote {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        target = self.resolve(path, [self.home])
        text = target.read_text()
        if text.count(old) != 1:
            raise ValueError(f"the text occurs {text.count(old)} times in {path}")
        target.write_text(text.replace(old, new))
        return f"edited {path}"

    def run(self, command: str, arguments: str) -> str:
        reply = self.execute(
            [command, *shlex.split(arguments)],
            cwd=self.home,
            timeout=COMMAND_SECONDS,
        )
        return reply.stdout + reply.stderr + f"\n(exit code {reply.returncode})"

    def tools(self) -> list[dict]:
        listed = [
            (
                "read_file",
                f"Read a file: `limit` lines (default {READ_LINES}) from line "
                "`offset` (default 1).",
                schema({"offset": "integer", "limit": "integer"}, path="string"),
            ),
            ("list_files", "List files by glob pattern.", schema(pattern="string")),
            (
                "grep",
                "Search a file or tree by regular expression, in the files whose "
                "name matches the glob `include`. Build trees are skipped.",
                schema({"include": "string"}, pattern="string", path="string"),
            ),
            (
                "write_file",
                "Create or replace a file in the working directory.",
                schema(path="string", content="string"),
            ),
            (
                "edit_file",
                "Replace text that occurs once in a file of the working directory.",
                schema(path="string", old="string", new="string"),
            ),
        ]
        listed += [
            (
                command.replace("-", "_"),
                f"Run `{command}` with arguments quoted as in a shell. No shell "
                "runs them.",
                schema(arguments="string"),
            )
            for command in self.commands
        ]
        return [
            {"name": name, "description": text, "inputSchema": shape}
            for name, text, shape in listed
        ]

    def call(self, name: str, arguments: dict) -> str:
        if self.turns_left <= 0:
            raise RuntimeError(TURN_LIMIT)
        self.turns_left -= 1
        for command in self.commands:
            if name == command.replace("-", "_"):
                return self.run(command, arguments["arguments"])
        if name not in ("read_file", "list_files", "grep", "write_file", "edit_file"):
            raise ValueError(f"unknown tool {name}")
        return getattr(self, name)(**arguments)

    def answer(self, method: str, params: dict) -> dict:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "session", "version": "1"},
            }
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "tools/call":
            failed = False
            try:
                text = self.call(params["name"], params.get("arguments") or {})
            except Exception as error:  # The model reads the error and adapts.
                text, failed = str(error), True
            if len(text) > MAX_REPLY:
                text = (
                    f"{text[:MAX_REPLY]}\n[Cut at {MAX_REPLY} of {len(text)} "
                    "characters. Narrow the request.]"
                )
            return {"content": [{"type": "text", "text": text}], "isError": failed}
        return {}


def main(arguments: list[str]) -> None:
    values: dict[str, list[str]] = {
        "--read": [],
        "--command": [],
        "--write": [],
        "--max-turns": ["0"],
    }
    for flag, value in zip(arguments[::2], arguments[1::2]):
        values[flag].append(value)
    session = Session(
        values["--write"][0],
        values["--read"],
        values["--command"],
        int(values["--max-turns"][-1]),
    )
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        result = session.answer(request["method"], request.get("params") or {})
        reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
