"""Sign-in to Claude and Codex from the page.

Each service's unmodified program runs under the user's account in a
pseudo-terminal, and its login stays where the program keeps it. Claude shows
a code on Anthropic's site, which passes from the page to the program's prompt
as a terminal would pass it; Codex shows a code to enter on OpenAI's site, so
nothing returns through the page. Nothing here stores or logs a sign-in code.

An API key is the other route. Codex stores its own; Claude's lies in a
private file and reaches runs through `environment`.

A program is installed for the user the first time its service is needed, so
that nobody installs a service they never use.
"""

import fcntl
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

from ..agents.claude_cli import get_account_status
from ..paths import STATE_DIR
from .model_servers import run_command, write_private

CODEX_VERSION = "0.166.0"
SERVICES = {
    "claude": {
        "label": "Claude",
        "command": ["claude", "auth", "login"],
        "install": "curl -fsSL https://claude.ai/install.sh | bash",
        "url": rb"https://\S+/oauth/authorize\?\S+",
    },
    "codex": {
        "label": "Codex",
        "command": ["codex", "login", "--device-auth"],
        "install": "npm install -g --prefix ~/.local "
        f"@openai/codex@{CODEX_VERSION}",
        "url": rb"https://auth\.openai\.com/\S+",
        "code": rb"\b[A-Z0-9]{4}-[A-Z0-9]{4,}\b",
    },
}
CLAUDE_KEY = STATE_DIR / "accounts" / "anthropic.key"
COLOR = re.compile(rb"\x1b\[[0-9;]*m")
STATUS_SECONDS = 300
INSTALL_SECONDS = 600
START_SECONDS = 30
FINISH_SECONDS = 60
# Wide enough that a program does not wrap an address.
WINDOW = struct.pack("HHHH", 50, 4000, 0, 0)

# Both installers place their program here; a program already on the path
# keeps its place.
os.environ["PATH"] = f"{os.environ['PATH']}:{Path.home() / '.local' / 'bin'}"

lock = threading.Lock()
session: tuple[subprocess.Popen, int] | None = None
statuses: dict[str, tuple[float, str | None]] = {}


class AccountError(Exception):
    """A sign-in that failed, with what the user should read."""


def ensure_installed(service: str) -> None:
    """Install a service's program if this account does not have it."""
    info = SERVICES[service]
    if shutil.which(info["command"][0]):
        return
    try:
        subprocess.run(
            info["install"],
            shell=True,
            capture_output=True,
            timeout=INSTALL_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pass
    if not shutil.which(info["command"][0]):
        raise AccountError(f"{info['label']} could not be installed")


def read_account(service: str) -> str | None:
    """The account a service's program is signed in to, as text."""
    if service == "claude":
        status = get_account_status()
        if status:
            return status.get("email") or status.get("authMethod")
        return "API key" if CLAUDE_KEY.exists() else None
    code, output = run_command(["codex", "login", "status"], 30)
    lines = output.strip().splitlines()
    return lines[-1] if code == 0 and lines else None


def account(service: str, fresh: bool = False) -> str | None:
    """The signed-in account, read again every few minutes."""
    read, value = statuses.get(service, (-STATUS_SECONDS, None))
    if fresh or time.monotonic() - read > STATUS_SECONDS:
        value = read_account(service)
        statuses[service] = (time.monotonic(), value)
    return value


def environment() -> dict[str, str]:
    """What a process needs to use a stored key."""
    if not CLAUDE_KEY.exists():
        return {}
    # The CLI keeps a run's Lean tools, which the API agent lacks.
    return {
        "ANTHROPIC_API_KEY": CLAUDE_KEY.read_text(),
        "CLAUDE_AUTH": "subscription",
    }


def set_api_key(service: str, key: str) -> None:
    ensure_installed(service)
    if service == "codex":
        login = subprocess.run(
            ["codex", "login", "--with-api-key"],
            input=key,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if login.returncode:
            raise AccountError("the key was not accepted")
    else:
        CLAUDE_KEY.parent.mkdir(mode=0o700, exist_ok=True)
        write_private(CLAUDE_KEY, key)
    account(service, fresh=True)


def close() -> None:
    global session
    if session:
        process, terminal = session
        session = None
        process.kill()
        process.wait()
        os.close(terminal)


def start(service: str) -> dict:
    """Start a sign-in and return the service's address, and for Codex the
    code to enter there."""
    global session
    ensure_installed(service)
    patterns = SERVICES[service]
    with lock:
        close()
        terminal, program_side = pty.openpty()
        fcntl.ioctl(program_side, termios.TIOCSWINSZ, WINDOW)
        process = subprocess.Popen(
            patterns["command"],
            stdin=program_side,
            stdout=program_side,
            stderr=program_side,
            cwd=Path.home(),
            env={**os.environ, "BROWSER": "/bin/true", "TERM": "dumb"},
            start_new_session=True,
        )
        os.close(program_side)
        session = process, terminal
        output, deadline = b"", time.monotonic() + START_SECONDS
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([terminal], [], [], 1)[0]:
                output += os.read(terminal, 65536)
            plain = COLOR.sub(b"", output)
            url = re.search(patterns["url"], plain)
            code = re.search(patterns.get("code", rb"^"), plain)
            if url and code:
                return {"url": url[0].decode(), "code": code[0].decode()}
        close()
    raise AccountError(f"{patterns['label']} gave no sign-in address")


def finish(code: str) -> None:
    """Give Claude's code to the waiting program, which completes the
    sign-in."""
    with lock:
        if not session:
            raise AccountError("no sign-in is waiting; start again")
        process, terminal = session
        os.write(terminal, code.strip().encode() + b"\r")
        deadline = time.monotonic() + FINISH_SECONDS
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([terminal], [], [], 1)[0]:
                try:
                    output = os.read(terminal, 65536)
                except OSError:
                    break
                if b"successful" in output.lower():
                    os.write(terminal, b"\r")
        succeeded = process.poll() == 0
        close()
    if not succeeded:
        raise AccountError("the code was not accepted; start again")
    account("claude", fresh=True)
