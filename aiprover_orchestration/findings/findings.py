"""The `findings` command of an explore task's agents: post to and read the
task's shared ledger. Run with `python3 -I`.

    findings post --kind KIND --topic TOPIC [--status STATUS] --title TITLE
                  (--body TEXT | --body-file FILE) [--file PATH]...
                  [--supersedes ID] [--refutes ID] [--hours H]
    findings claim TOPIC [--hours H] [--title TITLE] [--body TEXT]
    findings digest [--limit N]
    findings since [CURSOR] [--topic PREFIX] [--kind KIND]
    findings list [--topic PREFIX] [--kind KIND] [--agent AGENT]
    findings read ID...

The ledger is the JSON lines file `ledger.jsonl` of the folder FINDINGS_DIR,
which the task sets. It is an append-only log: an entry is never changed, and
a correction is a later entry that `supersedes` or `refutes` it. Each entry
has an id, its agent (FINDINGS_AUTHOR), a time, a kind (KINDS), a topic, a
slug such as `gap-b1/pencil-case` whose parts after a slash narrow it, a
status (STATUSES; required for a result, whose allowed statuses are in
RESULT_STATUSES), a title, a body, and the files it cites. Posts hold an
exclusive lock on the ledger, so that entries of agents posting at once
neither interleave nor overwrite each other, and ids follow the order of the
file.

Agents read a bounded summary instead of the whole log. `digest` prints, in
at most N characters (DIGEST_CHARS by default) however long the ledger grows,
the counts of entries by kind and status, the open alerts, the active leases,
and the latest positive and negative result of each topic. `since` prints the
entries after a cursor, the id of the last entry read, alerts first, and the
new cursor; without CURSOR it keeps the cursor in the file `findings_cursor`
of the agent's folder (SESSION_WRITE). Its reply is bounded too, and the
cursor then stops at the last entry printed, so that a check neither misses
nor repeats an entry.

A sketch or a claim is a lease on its topic for H hours (LEASE_HOURS by
default), which ends when it expires, when its holder posts a result on the
same topic, or, for a sketch, when its holder posts a later sketch. A negative
result or a counterexample is an alert when it refutes an entry or when its
topic is related, as prefix or extension, to a topic another agent holds; it
is addressed to those holders, to the author of the entry it refutes, and to
the agents whose entries supersede that entry. The addressees are computed
when the entry is posted and kept in its field `alert`.

A cited file must lie in the agent's folder and be at most MAX_FILE_BYTES; it
is copied into `files/` of the shared folder under the hash of its content,
which every agent of the task may read, and the entry keeps that name and the
original path. The body is never longer than MAX_BODY, so that material goes
into files. With FINDINGS_READ_ONLY set, as for the referee, the command
reads only.

Two extensions fit this layout without changing it. Islands, groups of agents
that each share a ledger and periodically copy their best entries to the
others, are ledgers in several shared folders and a migration step that posts
selected entries to another folder with `supersedes` naming their origin.
Verifier agents, which check high-impact claims, read the positives with
status `proved` and post a positive that supersedes, or a counterexample that
refutes, the entry they checked.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

KINDS = ("sketch", "claim", "positive", "negative", "counterexample", "note")
STATUSES = ("proved", "tested", "conjectured", "refuted", "pruned")
RESULT_STATUSES = {
    "positive": ("proved", "tested", "conjectured"),
    "negative": ("refuted", "pruned"),
    "counterexample": ("proved", "tested"),
}
LEASES = ("sketch", "claim")
TOPIC = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*")
LEDGER = "ledger.jsonl"
CURSOR = "findings_cursor"
LEASE_HOURS = 4.0
MAX_LEASE_HOURS = 24.0
MAX_TOPIC = 80
MAX_TITLE = 200
MAX_BODY = 20_000
MAX_FILE_BYTES = 10 * 2**20
MAX_FILES = 20
DIGEST_CHARS = 4000
MAX_REPLY = 20_000
# The body of an entry printed by `since`, beyond which `read` shows it.
PREVIEW_CHARS = 600
LINE_CHARS = 200


class Refused(ValueError):
    """An entry the ledger does not accept, with the reason."""


def related(one: str, other: str) -> bool:
    """Whether one topic equals, or narrows, the other."""
    return one == other or one.startswith(other + "/") or other.startswith(one + "/")


def leases(ledger: list[dict], now: float) -> dict[tuple[str, str], dict]:
    """The active leases of `ledger` at time `now`, by agent and topic."""
    held = {}
    for entry in ledger:
        key = (entry["agent"], entry["topic"])
        if entry["kind"] == "sketch":
            for other in [k for k, e in held.items() if k[0] == entry["agent"]]:
                if held[other]["kind"] == "sketch":
                    del held[other]
        if entry["kind"] in LEASES:
            held[key] = entry
        elif entry["kind"] in RESULT_STATUSES:
            held.pop(key, None)
    return {key: entry for key, entry in held.items() if entry["expires"] > now}


def addressees(ledger: list[dict], entry: dict) -> list[str] | None:
    """The agents a new entry alerts, or None if it is no alert."""
    if entry["kind"] not in ("negative", "counterexample"):
        return None
    agents = {agent for (agent, topic) in leases(ledger, entry["time"])
              if related(topic, entry["topic"])}  # fmt: skip
    if entry.get("refutes"):
        agents.add(ledger[entry["refutes"] - 1]["agent"])
        agents |= {e["agent"] for e in ledger if e.get("supersedes") == entry["refutes"]}
    agents.discard(entry["agent"])
    return sorted(agents) if agents or entry.get("refutes") else None


def store(shared: Path, path: Path) -> str:
    """Copy a file into `files/` of `shared` under the hash of its content;
    returns its name relative to `shared`."""
    data = path.read_bytes()
    suffix = path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", path.suffix) else ""
    name = Path("files") / (hashlib.sha256(data).hexdigest()[:32] + suffix)
    if not (shared / name).exists():
        (shared / "files").mkdir(parents=True, exist_ok=True)
        partial = shared / "files" / f".{os.getpid()}.{time.time_ns()}"
        partial.write_bytes(data)
        partial.replace(shared / name)
    return str(name)


def post(shared: Path, agent: str, kind: str, topic: str, title: str, body: str,
         status: str = "", files=(), supersedes: int = 0, refutes: int = 0,
         hours: float = LEASE_HOURS) -> dict:  # fmt: skip
    """Append an entry to the ledger of `shared`, citing `files`, given as
    (path, name) pairs; returns the entry, or raises Refused."""
    if kind not in KINDS:
        raise Refused(f"the kind is one of {', '.join(KINDS)}")
    if not TOPIC.fullmatch(topic) or len(topic) > MAX_TOPIC:
        raise Refused("the topic is a slug of lowercase letters, digits, '.', '_',"
                      f" and '-', in parts joined by '/', of at most {MAX_TOPIC}"
                      " characters")  # fmt: skip
    if kind in RESULT_STATUSES and status not in RESULT_STATUSES[kind]:
        raise Refused(f"the status of a {kind} is one of {', '.join(RESULT_STATUSES[kind])}")
    if status and status not in STATUSES:
        raise Refused(f"the status is one of {', '.join(STATUSES)}")
    title = " ".join(title.split())
    if not title or len(title) > MAX_TITLE:
        raise Refused(f"the title needs 1 to {MAX_TITLE} characters")
    if not body.strip() or len(body) > MAX_BODY:
        raise Refused(f"the body needs 1 to {MAX_BODY} characters; put longer"
                      " material in a file and cite it with --file")  # fmt: skip
    if refutes and kind not in ("negative", "counterexample"):
        raise Refused("only a negative result or a counterexample refutes an entry")
    if not 0 < hours <= MAX_LEASE_HOURS:
        raise Refused(f"a lease lasts more than 0 and at most {MAX_LEASE_HOURS:g} hours")
    cited = [{"path": name, "ref": store(shared, path)} for path, name in files]
    shared.mkdir(parents=True, exist_ok=True)
    with open(shared / LEDGER, "a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        ledger = [json.loads(line) for line in file if line.strip()]
        for reference in (supersedes, refutes):
            if reference and not 0 < reference <= len(ledger):
                raise Refused(f"there is no entry {reference}")
        now = time.time()
        entry = {"id": len(ledger) + 1, "agent": agent, "time": round(now, 3),
                 "kind": kind, "topic": topic, "status": status, "title": title,
                 "body": body, "files": cited}  # fmt: skip
        if supersedes:
            entry["supersedes"] = supersedes
        if refutes:
            entry["refutes"] = refutes
        if kind in LEASES:
            entry["expires"] = round(now + hours * 3600)
        alert = addressees(ledger, entry)
        if alert is not None:
            entry["alert"] = alert
        file.write(json.dumps(entry) + "\n")
        file.flush()
    return entry


def entries(shared: Path) -> list[dict]:
    """The ledger of `shared`, in order of posting."""
    if not (shared / LEDGER).exists():
        return []
    with open(shared / LEDGER) as file:
        fcntl.flock(file, fcntl.LOCK_SH)
        return [json.loads(line) for line in file if line.strip()]


def heading(entry: dict) -> str:
    """One line of an entry."""
    status = f" {entry['status']}" if entry.get("status") else ""
    links = "".join(f" {field} #{entry[field]}" for field in ("supersedes", "refutes")
                    if entry.get(field))  # fmt: skip
    files = f" ({len(entry['files'])} files)" if entry["files"] else ""
    line = (f"#{entry['id']} [{entry['kind']}{status}] {entry['topic']}"
            f" {entry['agent']}{links}: {entry['title']}{files}")  # fmt: skip
    return line[:LINE_CHARS]


def alert_line(entry: dict) -> str:
    to = ", ".join(entry["alert"]) or "everyone"
    return f"ALERT to {to}: {heading(entry)}"[:LINE_CHARS]


def show(shared: Path, entry: dict, preview: int = 0) -> str:
    """An entry, its body cut at `preview` characters if given, with the
    paths of its files."""
    body = entry["body"].rstrip()
    if preview and len(body) > preview:
        body = body[:preview] + f" [... `findings read {entry['id']}` for the rest]"
    lines = [heading(entry), body]
    lines += [f"File {f['path']}: {shared / f['ref']}" for f in entry["files"]]
    return "\n".join(lines)


def section(title: str, lines: list[str], budget: int) -> str:
    """A titled list of lines within `budget` characters, saying how many
    lines it leaves out."""
    text = title + "\n"
    for shown, line in enumerate(lines):
        if len(text) + len(line) + 40 > budget:
            return text + f"({len(lines) - shown} more)\n"
        text += line + "\n"
    return text if lines else text + "(none)\n"


def digest(ledger: list[dict], now: float, limit: int = DIGEST_CHARS) -> str:
    """The summary of `ledger` at time `now`, in at most `limit` characters:
    counts, open alerts, active leases, and each topic's latest results, the
    most recently active topics first. Each part has its share of `limit`."""
    later = {}  # an entry's id -> the later entries that correct it
    for entry in ledger:
        for field in ("supersedes", "refutes"):
            if entry.get(field):
                later.setdefault(entry[field], []).append(f"{field[:-1]}d by #{entry['id']}")
    kinds = [f"{n} {k}" for k in KINDS if (n := sum(e["kind"] == k for e in ledger))]
    statuses = [f"{n} {s}" for s in STATUSES if (n := sum(e.get("status") == s for e in ledger))]
    counts = (f"Ledger: {len(ledger)} entries ({', '.join(kinds) or 'none'});"
              f" status {', '.join(statuses) or 'none'}.\n")  # fmt: skip
    alerts = [alert_line(e) for e in reversed(ledger) if "alert" in e and e["id"] not in later]
    active = sorted(leases(ledger, now).values(), key=lambda e: e["topic"])
    held = [f"{e['topic']} {e['agent']} {(e['expires'] - now) / 3600:.1f}h: "
            f"{e['title']}"[:LINE_CHARS] for e in active]  # fmt: skip
    topics = {}
    for entry in reversed(ledger):
        if entry["kind"] in RESULT_STATUSES and entry["id"] not in later:
            side = "positive" if entry["kind"] == "positive" else "negative"
            topics.setdefault(entry["topic"], {}).setdefault(side, entry)
    results = [heading(entry) for sides in topics.values() for entry in sides.values()]
    corrected = [f"#{i} {', '.join(notes)}"[:LINE_CHARS] for i, notes in reversed(later.items())]
    rest = limit - len(counts)
    text = counts + "".join([
        section("Open alerts, newest first:", alerts, rest // 4),
        section("Active leases (topic, holder, hours left):", held, rest // 4),
        section("Latest results by topic, most recent first:", results, rest * 3 // 8),
        section("Corrected entries, newest first:", corrected, rest // 8),
    ])  # fmt: skip
    return text[:limit]


def since(shared: Path, ledger: list[dict], cursor: int, agent: str,
          topic: str = "", kind: str = "") -> tuple[str, int]:  # fmt: skip
    """The entries after `cursor` that match the filters, alerts to `agent`
    first, within MAX_REPLY characters, and the new cursor."""
    new = ledger[cursor:]
    alerts = [alert_line(e) for e in new if agent in e.get("alert", ())]
    text = "\n".join(alerts[:20]) + ("\n\n" if alerts else "")
    last = cursor
    for entry in new:
        if entry["topic"].startswith(topic) and kind in ("", entry["kind"]):
            shown = show(shared, entry, PREVIEW_CHARS) + "\n\n"
            if len(text) + len(shown) > MAX_REPLY and last > cursor:
                break
            text += shown
        last = entry["id"]
    if not new:
        text += "No new entries.\n"
    return text + f"Cursor: {last}", last


def cited_files(paths: list[str]) -> list[tuple[Path, str]]:
    """The files to copy, each with its name relative to the agent's folder."""
    root = Path(os.environ.get("SESSION_WRITE") or Path.cwd()).resolve()
    if len(paths) > MAX_FILES:
        raise Refused(f"at most {MAX_FILES} files per entry")
    files = []
    for given in paths:
        path = (Path.cwd() / given).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise Refused(f"{given} is not a file of your own folder")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Refused(f"{given} exceeds {MAX_FILE_BYTES // 2**20} MB")
        files.append((path, str(path.relative_to(root))))
    return files


def main(arguments: list[str]) -> str:
    parser = argparse.ArgumentParser(prog="findings")
    actions = parser.add_subparsers(dest="action", required=True)
    posting = actions.add_parser("post", help="add an entry to the ledger")
    posting.add_argument("--kind", choices=KINDS, required=True)
    posting.add_argument("--topic", required=True)
    posting.add_argument("--status", choices=STATUSES, default="")
    posting.add_argument("--title", required=True)
    bodies = posting.add_mutually_exclusive_group(required=True)
    bodies.add_argument("--body")
    bodies.add_argument("--body-file", type=Path)
    posting.add_argument("--file", action="append", default=[])
    posting.add_argument("--supersedes", type=int, default=0)
    posting.add_argument("--refutes", type=int, default=0)
    posting.add_argument("--hours", type=float, default=LEASE_HOURS)
    claiming = actions.add_parser("claim", help="lease a topic")
    claiming.add_argument("topic")
    claiming.add_argument("--hours", type=float, default=LEASE_HOURS)
    claiming.add_argument("--title", default="")
    claiming.add_argument("--body", default="")
    summary = actions.add_parser("digest", help="a bounded summary of the ledger")
    summary.add_argument("--limit", type=int, default=DIGEST_CHARS)
    new = actions.add_parser("since", help="the entries after a cursor")
    new.add_argument("cursor", type=int, nargs="?")
    listing = actions.add_parser("list", help="the entries, one line each")
    listing.add_argument("--agent")
    for command in (new, listing):
        command.add_argument("--topic", default="")
        command.add_argument("--kind", choices=KINDS, default="")
    reading = actions.add_parser("read", help="entries in full")
    reading.add_argument("ids", type=int, nargs="+")
    options = parser.parse_args(arguments)
    if not os.environ.get("FINDINGS_DIR"):
        sys.exit("findings: FINDINGS_DIR is not set; only an explore task has a ledger")
    shared = Path(os.environ["FINDINGS_DIR"])
    agent = os.environ.get("FINDINGS_AUTHOR", "unknown")
    if options.action in ("post", "claim"):
        if os.environ.get("FINDINGS_READ_ONLY"):
            sys.exit("findings: this session reads the ledger only")
        try:
            if options.action == "claim":
                entry = post(shared, agent, "claim", options.topic,
                             options.title or f"Claim on {options.topic}",
                             options.body or "Working on this topic.",
                             hours=options.hours)  # fmt: skip
            else:
                body = options.body or (options.body_file.read_text() if options.body_file else "")
                entry = post(shared, agent, options.kind, options.topic, options.title,
                             body, options.status, cited_files(options.file),
                             options.supersedes, options.refutes, options.hours)  # fmt: skip
        except (Refused, OSError) as error:
            sys.exit(f"findings: {error}")
        return f"posted {heading(entry)}" + "".join(
            f"\nFile {f['path']}: {shared / f['ref']}" for f in entry["files"]
        )
    ledger = entries(shared)
    if options.action == "digest":
        return digest(ledger, time.time(), max(500, options.limit))
    if options.action == "since":
        mine = Path(os.environ.get("SESSION_WRITE") or Path.cwd()) / CURSOR
        cursor = options.cursor
        if cursor is None:
            cursor = int(mine.read_text()) if mine.exists() else 0
        text, last = since(shared, ledger, max(0, cursor), agent, options.topic, options.kind)
        if options.cursor is None:
            mine.write_text(str(last))
        return text
    if options.action == "list":
        chosen = [
            entry for entry in ledger
            if options.kind in ("", entry["kind"])
            and options.agent in (None, entry["agent"])
            and (not options.topic or entry["topic"].startswith(options.topic))
        ]  # fmt: skip
        return ("\n".join(map(heading, chosen)) or "The ledger has no such entries.")[-MAX_REPLY:]
    missing = [str(i) for i in options.ids if not 0 < i <= len(ledger)]
    if missing:
        sys.exit(f"findings: no entry {', '.join(missing)}")
    return "\n\n".join(show(shared, ledger[i - 1]) for i in options.ids)[:MAX_REPLY]


if __name__ == "__main__":
    print(main(sys.argv[1:]))
