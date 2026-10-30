"""Web server for submitting problems to the orchestration and following runs.

Start (single process; the job worker lives in it):
    uvicorn aiprover_orchestration.server.app:app --host 0.0.0.0 --port 8443

Access is open by default: the server is run by its own users, no token
is required, submissions are queued without approval, and every visitor
sees and may cancel every run.

Two pages share the API: the user page `/user` (where `/` leads) offers the
DGX and the open and OpenAI math problems, and lists the runs submitted from
it; the development page `/dev` offers every GPU group, model and dataset and
lists every run (`PAGES`; API calls name the page with `?page=dev`, and a call
that names none is the user page's).

With `QUERY_SERVER_AUTH=on` in the environment, users authenticate with a
bearer token from `configs/server/tokens.json` (`{"<token>": "<user
name>"}`), sent as `Authorization: Bearer <token>` or as the `token` cookie
that `POST /api/login` sets for the web page, and a submission awaits
approval by a token holder.

Claude roles run on the `claude` login of the account that runs the server.
The user page therefore accepts them only on a per-user server
(scripts/user/aiprover-server, `QUERY_SERVER_PER_USER=on`), where that account
is the submitting user's own.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel

from ..agents.claude_cli import get_account_status
from ..library import api as library_api
from ..orchestrator import libraries
from ..orchestrator.config import FILE_HEADER
from ..orchestrator.reports.informal import RESULT_ROW_SUFFIX
from ..orchestrator.trace import Trace
from ..paths import (
    CONFIG_DIR,
    HOME_DIR,
    LIBRARIES_DIR,
    LOGS_DIR,
    RESULTS_DIR,
    STATE_DIR,
    TEMP_DIR,
)

from . import models, settings, vista
from .jobs import (
    CONFIG,
    DATA_DIR,
    DATABASE,
    OPEN_STATES,
    PROBLEM_DIR,
    JobStore,
    Worker,
    endpoint_status,
    job_selection,
)
from .vista import control_master_running

TOKENS = CONFIG_DIR / "server" / "tokens.json"
AUTH_REQUIRED = os.environ.get("QUERY_SERVER_AUTH", "off") == "on"
PER_USER = os.environ.get("QUERY_SERVER_PER_USER", "off") == "on"
GUEST = "guest"
INDEX_PAGE = Path(__file__).parent / "static" / "index.html"
MAX_OPEN_JOBS_PER_USER = 3
MAX_QUEUED_JOBS = 20
MAX_LIBRARY_CHARS = 2_000_000
# A bound on a submitted statement or proof against oversized requests; the
# page warns at lengths that strain the context of the agents long before it.
MAX_TEXT_CHARS = 1_000_000
# Bounds on an imported run: the upload and its unpacked files.
MAX_IMPORT_BYTES = 2**30
MAX_UNPACKED_BYTES = 2**32
MAX_IMPORTS_PER_USER = 20
# Libraries a run may build on (orchestrator/libraries.py); uploads are kept
# per run under uploads/.
LIBRARY_DIR = LIBRARIES_DIR
LIBRARY_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.lean$")
RESULT_PAGES = ("trace.html", "trace_replay.html", "summary.json")
# Files at the root of a run, by the name the page links them under.
ROOT_FILES = {
    "standalone.lean": "*_standalone.lean",
    "result.jsonl": f"*{RESULT_ROW_SUFFIX}",
}
# Problem groups of this server, {"<start of a problem id>": "<group>"}. They
# are specific to its users, so they lie in its state and not in the repository.
GROUPS = STATE_DIR / "groups.json"
TERMINAL_STATES = ("finished", "cancelled", "rejected")
# Statuses of a finished run that settle its theorem; such a run is final.
CONCLUSIVE = ("proved", "disproved")

LOGS_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "query_server.log"),
    ],
)
store = JobStore()
worker = Worker(store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield


app = FastAPI(title="AIProver orchestration", lifespan=lifespan)


class RevalidatedFiles(StaticFiles):
    """Static files that a browser checks for changes on each load."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount(
    "/static", RevalidatedFiles(directory=INDEX_PAGE.parent), name="static"
)


# ── Authentication ─────────────────────────────────────────────────────────

_token_cache: tuple[float, dict] = (0.0, {})


def load_tokens() -> dict[str, str]:
    """Token → user name; re-read when the file changes, so no restart is
    needed."""
    global _token_cache
    try:
        modified = TOKENS.stat().st_mtime
    except OSError:
        return {}
    if modified != _token_cache[0]:
        try:
            tokens = json.loads(TOKENS.read_text() or "{}")
        except json.JSONDecodeError:
            logging.error(f"{TOKENS} is not valid JSON; no token is accepted")
            tokens = {}
        _token_cache = (modified, tokens)
    return _token_cache[1]


def user_for_token(token: str) -> str | None:
    for known, user in load_tokens().items():
        if secrets.compare_digest(token.encode(), known.encode()):
            return user
    return None


def token_user(request: Request) -> str | None:
    """User of the request's token (header or cookie), or None."""
    header = request.headers.get("authorization", "")
    token = (
        header[7:]
        if header.lower().startswith("bearer ")
        else request.cookies.get("token", "")
    )
    return user_for_token(token) if token else None


def current_user(request: Request) -> str:
    user = token_user(request)
    if user is None and not AUTH_REQUIRED:
        return GUEST
    if user is None:
        raise HTTPException(401, "missing or unknown token")
    return user


def approver(request: Request) -> str:
    user = token_user(request)
    if user is None:
        raise HTTPException(403, "approval requires a token")
    return user


def visible_owner(user: str) -> str | None:
    """Owner whose runs `user` sees; None (all runs) without authentication."""
    return user if AUTH_REQUIRED else None


# Per-user servers (scripts/user/aiprover-server) whose state the owner of
# the checkout keeps in users/<account>: the shared server's development
# page lists their runs read-only, as `<account>:<run_id>`. Their actions
# remain with the user's own server, which runs on the user's Claude login.
USERS_DIR = HOME_DIR / "users"
user_stores: dict[str, JobStore] = {}


def read_user_store(account: str, read):
    """`read` applied to the run records of an account's server, opened
    read-only; None if they are not readable."""
    try:
        if account not in user_stores:
            user_stores[account] = JobStore(
                USERS_DIR / account / DATABASE.relative_to(HOME_DIR),
                readonly=True,
            )
        return read(user_stores[account])
    except sqlite3.Error:
        return None


def user_job(account: str, job: dict | None) -> dict | None:
    """A job of an account's server, keyed by `<account>:<run_id>` and
    carrying the state directory it lives in."""
    return job and job | {
        "run_id": f"{account}:{job['run_id']}",
        "owner": account,
        "local_id": job["run_id"],
        "home": USERS_DIR / account,
    }


def user_jobs(account: str) -> list[dict]:
    return [
        user_job(account, job)
        for job in read_user_store(account, JobStore.jobs) or []
    ]


def other_users() -> list[str]:
    """Accounts whose servers the shared server shows; none on a per-user
    server."""
    if PER_USER or not USERS_DIR.is_dir():
        return []
    return sorted(
        path.name
        for path in USERS_DIR.iterdir()
        if (path / DATABASE.relative_to(HOME_DIR)).exists()
    )


def find_job(run_id: str) -> dict | None:
    """A job of this server, or `<account>:<run_id>` of a user's server."""
    account, separator, local = run_id.partition(":")
    if not separator:
        return store.get(run_id)
    if account not in other_users():
        return None
    return user_job(account, read_user_store(account, lambda s: s.get(local)))


def current_record(job: dict) -> dict | None:
    """The job's record as its server holds it now."""
    if "home" not in job:
        return store.get(job["run_id"])
    return read_user_store(job["owner"], lambda s: s.get(job["local_id"]))


def run_dir(job: dict, folder: Path) -> Path:
    """The run's directory in RESULTS_DIR, LOGS_DIR or TEMP_DIR of its
    server."""
    home = job.get("home", HOME_DIR)
    return home / folder.relative_to(HOME_DIR) / job.get("local_id", job["run_id"])


def servable(job: dict, path: Path, *roots: Path) -> bool:
    """Whether `path` exists and the server reads it for the job. A user's
    run admits only files that resolve inside the user's state (or `roots`),
    since the user may replace its files with links to files of the server's
    account."""
    if not path.exists():
        return False
    if "home" not in job:
        return True
    target = path.resolve()
    return any(
        target.is_relative_to(root.resolve()) for root in (job["home"], *roots)
    )


def owned_job(run_id: str, user: str = Depends(current_user)) -> dict:
    job = find_job(run_id)
    if job is None or visible_owner(user) not in (None, job["owner"]):
        raise HTTPException(404, "no such run")
    return job


def own_job(job: dict = Depends(owned_job)) -> dict:
    """A job this server runs; a user's own server manages the others."""
    if "home" in job:
        raise HTTPException(
            403, f"run is managed by the server of {job['owner']}"
        )
    return job


class Login(BaseModel):
    token: str


@app.exception_handler(HTTPException)
async def retry_cookieless_navigation(request: Request, error: HTTPException):
    """Repeat a page load the strict cookie missed, from this site's own page.

    A link in a sandboxed trace page has an opaque origin, so its navigation
    comes without the login cookie. The repeat is same-origin and carries it;
    a viewer who is not signed in then receives the 401 itself.
    """
    if (
        error.status_code == 401
        and request.headers.get("sec-fetch-dest") == "document"
        and request.headers.get("sec-fetch-site") != "same-origin"
    ):
        return HTMLResponse(
            "<script>location.replace(location.href)</script>", 401
        )
    return await http_exception_handler(request, error)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response) -> dict:
    user = user_for_token(body.token)
    if user is None:
        raise HTTPException(401, "unknown token")
    response.set_cookie(
        "token",
        body.token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=30 * 86400,
    )
    return {"user": user}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("token")
    return {}


app.include_router(library_api.router, dependencies=[Depends(current_user)])


# ── Problems and runs ──────────────────────────────────────────────────────


def book_label(statement: str) -> str:
    match = re.search(r"book label `([^`]+)`", statement)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class Page:
    """What a page offers: GPU groups (the first is the default), AIProver
    versions and datasets of data/ (None: all), default model choices,
    whether its run list holds only the runs submitted from it, whether its
    runs may use Claude roles on the server's login, and whether it lists
    the runs of per-user servers (read-only)."""

    gpu_groups: tuple[str, ...]
    versions: tuple[str, ...] | None = None
    datasets: tuple[str, ...] | None = None
    defaults: dict = field(default_factory=dict)
    own_runs: bool = False
    claude: bool = True
    user_servers: bool = False


PAGES = {
    "dev": Page(
        tuple(sorted(vista.GPU_GROUPS, key=lambda g: g != vista.DEFAULT_GROUP)),
        user_servers=True,
    ),
    "user": Page(
        ("dgx",),
        versions=("dgx",),
        datasets=("open_problems.jsonl", "openai_math.jsonl"),
        defaults={
            "subagent": {"provider": "aiprover", "model": "dgx", "effort": ""}
        },
        own_runs=True,
        claude=PER_USER,
    ),
}


DEFAULT_PAGE = "user"


def page_of(page: str = DEFAULT_PAGE) -> Page:
    """The page named by the `page` query parameter."""
    if page not in PAGES:
        raise HTTPException(400, f"unknown page {page!r}")
    return PAGES[page]


def read_dataset(page: Page = PAGES["dev"]) -> dict[str, dict]:
    """Problems of the page's JSONL files in data/, by UUID, each with its
    file."""
    problems = {}
    for path in sorted(DATA_DIR.glob("*.jsonl")):
        if page.datasets is not None and path.name not in page.datasets:
            continue
        with open(path) as f:
            for line in f:
                row = json.loads(line)
                problems[row["uuid"]] = row | {"_dataset": str(path)}
    return problems


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(f"/{DEFAULT_PAGE}")


@app.get("/user")
@app.get("/dev")
def serve_page() -> FileResponse:
    """Both pages; the page reads which one it is from its path."""
    return FileResponse(INDEX_PAGE)


# The account of the server's Claude login, read at most every
# ACCOUNT_SECONDS rather than by a CLI process on each page load.
ACCOUNT_SECONDS = 300
account_cache: dict = {"read": float("-inf"), "email": None}
account_lock = threading.Lock()


def claude_account() -> str | None:
    with account_lock:
        if time.monotonic() - account_cache["read"] > ACCOUNT_SECONDS:
            account_cache["email"] = (get_account_status() or {}).get("email")
            account_cache["read"] = time.monotonic()
        return account_cache["email"]


@app.get("/api/me")
def me(request: Request, user: str = Depends(current_user)) -> dict:
    return {
        "user": user,
        "auth_required": AUTH_REQUIRED,
        "approver": AUTH_REQUIRED and token_user(request) is not None,
        # The Claude account of a per-user server, which is its user's own;
        # a shared server does not show its account.
        "per_user": PER_USER,
        "claude_account": claude_account() if PER_USER else None,
    }


def proxy_pool(group: vista.GpuGroup) -> list[dict]:
    """The model servers behind the group's reasoning proxy, with their
    health and the requests in flight through it; empty if it is down."""
    url = f"http://127.0.0.1:{group.proxy_port}/upstreams"
    try:
        with urllib.request.urlopen(url, timeout=3) as reply:
            return json.load(reply)["upstreams"]
    except (OSError, ValueError, KeyError):
        return []


@app.get("/api/health")
def health(
    user: str = Depends(current_user), page: Page = Depends(page_of)
) -> dict:
    running = store.in_states("running", "cancelling")
    groups = {}
    for name, group in vista.GPU_GROUPS.items():
        if group.serverless:
            continue
        endpoint_up, endpoint_message = endpoint_status(
            bring_up=False, group=group
        )
        server_jobs = vista.server_job_details(group)
        groups[name] = {
            "endpoint_up": endpoint_up,
            "endpoint": endpoint_message,
            "server_jobs": server_jobs,
            "vista_jobs": (
                None
                if server_jobs is None
                else {job["id"]: job["state"] for job in server_jobs}
            ),
            "vista": worker.vista_messages.get(name, "not checked"),
            "persistent": not group.job_name,
            "pool": proxy_pool(group),
            "borrows": list(group.borrows),
            "lent_to": [
                other.name
                for other in vista.GPU_GROUPS.values()
                if name in other.borrows
            ],
        }
    # A group whose own server is down is served while a server it
    # borrows answers.
    for name, group in vista.GPU_GROUPS.items():
        if group.serverless:
            continue
        lenders = [
            lender for lender in group.borrows if groups[lender]["endpoint_up"]
        ]
        if lenders and not groups[name]["endpoint_up"]:
            groups[name]["endpoint_up"] = True
            groups[name]["endpoint"] += f"; served by {', '.join(lenders)}"
    main = groups[vista.DEFAULT_GROUP]
    return {
        "control_master": control_master_running(),
        "endpoint_up": main["endpoint_up"],
        "endpoint": main["endpoint"],
        "vista_jobs": {
            job_id: state
            for group in groups.values()
            for job_id, state in (group["vista_jobs"] or {}).items()
        },
        "vista": worker.vista_message,
        "groups": {
            name: group
            for name, group in groups.items()
            if name in page.gpu_groups
        },
        "awaiting_approval": len(store.in_states("awaiting_approval")),
        "queued": len(store.in_states("queued")),
        "running": len(running),
    }


@app.get("/api/problems")
def problems(
    user: str = Depends(current_user), page: Page = Depends(page_of)
) -> list[dict]:
    return [
        {
            "uuid": uuid,
            "domain": row.get("domain"),
            "source": row.get("source_name"),
            "label": book_label(row.get("informal_statement", "")),
        }
        for uuid, row in read_dataset(page).items()
    ]


class Selection(BaseModel):
    provider: str
    model: str
    effort: str = ""
    # Lean tools of a Claude role; None takes the role's default.
    tools: bool | None = None
    judge_tools: bool | None = None


class Submission(BaseModel):
    name: str = ""  # run name; the run id if free
    group: str = ""  # problem group; by default that of the dataset
    uuid: str = ""
    statement: str = ""
    proof: str = ""
    orchestrator: Selection | None = None
    auditor: Selection | None = None
    reviewer: Selection | None = None
    writer: Selection | None = None
    subagent: Selection | None = None
    handback: Selection | None = None
    gpu_group: str = vista.DEFAULT_GROUP
    library: str = ""  # a file in libraries/
    library_text: str = ""  # or an uploaded library
    extend_library: bool = False
    new_library: str = ""  # or a library this run starts, in libraries/
    after: str = ""  # a run that must end first
    settings: dict = {}  # orchestrator settings (settings.SETTINGS)
    page: str = DEFAULT_PAGE  # the page submitting (PAGES)


@app.get("/api/models")
def model_catalog(
    user: str = Depends(current_user), page: Page = Depends(page_of)
) -> dict:
    catalog = models.catalog()
    return catalog | {
        "aiprover": [
            version
            for version in catalog["aiprover"]
            if page.versions is None or version["model"] in page.versions
        ],
        "default": catalog["default"] | page.defaults,
        "gpu_groups": list(page.gpu_groups),
        "default_gpu_group": page.gpu_groups[0],
        "settings": settings.catalog(json.loads(CONFIG.read_text())),
    }


def run_name(text: str) -> str:
    """`text` in the characters of a run id, which names files and URLs."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.-")[:80]


def new_run_id(suffix: str, name: str = "") -> str:
    """A free run id, reserved by creating its result directory."""
    base = run_name(name) or f"srv_{time.strftime('%Y%m%d_%H%M%S')}_{suffix}"
    run_id, count = base, 1
    while True:
        if not store.get(run_id):
            try:
                (RESULTS_DIR / run_id).mkdir(parents=True)
                return run_id
            except FileExistsError:
                pass
        count += 1
        run_id = f"{base}_{count}"


def write_problem(run_id: str, row: dict) -> Path:
    """The dataset of a run's problem, written from `row`."""
    dataset = PROBLEM_DIR / f"{run_id}.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(json.dumps(row) + "\n")
    return dataset


def statement_title(statement: str) -> str:
    """The first line with words: a statement may open with a rule line."""
    return next(
        (line for line in statement.splitlines() if re.search(r"\w", line)),
        "",
    ).strip()[:120]


def claude_allowed(page: Page, selection: dict) -> bool:
    """Whether the page's runs may use `selection` on the server's login."""
    return page.claude or not any(
        choice["provider"] == "claude" for choice in selection.values()
    )


def check_capacity(user: str) -> None:
    """Refuse a new or resumed run beyond the per-user and queue limits."""
    open_jobs = [job for job in store.jobs(user) if job["state"] in OPEN_STATES]
    if AUTH_REQUIRED and len(open_jobs) >= MAX_OPEN_JOBS_PER_USER:
        raise HTTPException(
            429, f"at most {MAX_OPEN_JOBS_PER_USER} open runs per user"
        )
    if len(store.in_states("awaiting_approval", "queued")) >= MAX_QUEUED_JOBS:
        raise HTTPException(503, "queue is full")


@app.post("/api/runs", status_code=201)
def submit(body: Submission, user: str = Depends(current_user)) -> dict:
    check_capacity(user)

    page = page_of(body.page)
    if body.gpu_group not in page.gpu_groups:
        raise HTTPException(400, f"unknown GPU group {body.gpu_group!r}")
    try:
        selection = models.validate(
            page.defaults
            | {
                role: getattr(body, role).model_dump(exclude_none=True)
                for role in models.SELECTABLE_ROLES
                if getattr(body, role)
            }
        )
    except ValueError as error:
        raise HTTPException(400, str(error))
    if not claude_allowed(page, selection):
        raise HTTPException(
            400,
            "Claude roles run on your own Claude login: start your own server "
            "with scripts/user/aiprover-server (docs/server.md)",
        )
    try:
        run_settings = settings.validate(body.settings)
    except ValueError as error:
        raise HTTPException(400, str(error))
    if page.versions is not None and not (
        models.checkpoint_names(selection) <= set(page.versions)
    ):
        raise HTTPException(
            400, f"this page offers AIProver {', '.join(page.versions)} only"
        )
    if vista.GPU_GROUPS[body.gpu_group].serverless and (
        models.checkpoint_names(selection)
    ):
        raise HTTPException(
            400,
            f"group {body.gpu_group!r} has no model server: every role, "
            "including the subagent, must use Claude or a hosted model",
        )
    group = vista.GPU_GROUPS[body.gpu_group]
    if not group.job_name and models.checkpoint_of(selection) is not None:
        # A persistent server serves its own version; a Vista checkpoint
        # needs a group with Vista server jobs.
        raise HTTPException(
            400,
            f"group {body.gpu_group!r} serves AIProver "
            f"{', '.join(sorted(models.PERSISTENT_VERSIONS))} only",
        )

    if body.uuid:
        row = read_dataset(page).get(body.uuid)
        if row is None:
            raise HTTPException(400, f"unknown problem {body.uuid}")
        # JiatuBook ids end in a problem number; other datasets use the whole
        # id.
        suffix = (
            body.uuid.rsplit("_", 1)[-1]
            if body.uuid.startswith("JiatuBook_")
            else body.uuid
        )
        run_id = new_run_id(suffix, body.name)
        uuid, dataset = body.uuid, row["_dataset"]
        title = book_label(row["informal_statement"]) or body.uuid
    else:
        statement, proof = body.statement.strip(), body.proof.strip()
        if not statement:
            raise HTTPException(
                400, "give a dataset uuid or an informal statement"
            )
        if max(len(statement), len(proof)) > MAX_TEXT_CHARS:
            raise HTTPException(413, "statement or proof too long")
        run_id = new_run_id("user", body.name)
        uuid = f"user_{run_id}"
        row = {
            "uuid": uuid,
            "source_name": "user",
            "domain": "user",
            "informal_statement": statement,
            "informal_proof": proof or "(No informal proof is given.)",
        }
        dataset = write_problem(run_id, row)
        title = statement_title(statement)

    try:
        options = library_options(body, run_id) | run_settings
    except HTTPException:
        # A refused submission keeps no claim on its name.
        (RESULTS_DIR / run_id).rmdir()
        raise
    store.add(
        {
            "run_id": run_id,
            "owner": user,
            "uuid": uuid,
            "title": title,
            "dataset": str(dataset),
            "state": "awaiting_approval" if AUTH_REQUIRED else "queued",
            "submitted": time.time(),
            "agents": json.dumps(selection),
            "options": json.dumps(options) if options else None,
            "gpu_group": body.gpu_group,
            "page": body.page,
            "problem_group": body.group.strip()[:80] or None,
        }
    )
    return {"run_id": run_id, "state": store.get(run_id)["state"]}


def unpack_run(archive: zipfile.ZipFile, target: Path) -> None:
    """Restore a zip of a run folder or of an export into `target`."""
    files = {
        info: PurePosixPath(info.filename)
        for info in archive.infolist()
        if not info.is_dir() and not info.filename.startswith("__MACOSX/")
    }
    if not files:
        raise ValueError("the archive is empty")
    if sum(info.file_size for info in files) > MAX_UNPACKED_BYTES:
        raise ValueError("the archive unpacks to too much data")
    if any(path.is_absolute() or ".." in path.parts for path in files.values()):
        raise ValueError("the archive has paths outside its folder")
    # Both forms may hold one folder named after the run.
    tops = {path.parts[0] for path in files.values()}
    nested = len(tops) == 1 and all(len(p.parts) > 1 for p in files.values())
    for info, path in files.items():
        relative = restored_path(path.parts[nested:])
        if relative is None:
            continue
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target / relative, "wb") as out:
            shutil.copyfileobj(source, out)


def first_text(key: str, *records: dict) -> str:
    """The first text field `key` of imported records; empty if none."""
    values = (record.get(key) for record in records)
    return next((value for value in values if isinstance(value, str)), "")


def read_run(unpacked: Path) -> dict:
    """The job fields of an unpacked run. A trace in another form (gzip,
    lines) becomes the trace.json that resuming reads."""
    trace = Trace.load(unpacked / "trace.json") or {}
    summary_path = unpacked / "summary.json"
    summary = (
        json.loads(summary_path.read_text()) if summary_path.exists() else {}
    )
    if not trace and not (unpacked / "trace.html").exists():
        raise ValueError("the archive holds no run trace")
    if trace and not (unpacked / "trace.json").exists():
        (unpacked / "trace.json").write_text(json.dumps(trace))
        (unpacked / "trace.json.gz").unlink(missing_ok=True)
        (unpacked / "trace.jsonl").unlink(missing_ok=True)
    problem = trace.get("problem") or {}
    uuid = first_text("uuid", problem, summary) or "imported"
    statement = first_text("informal_statement", problem)
    status = first_text("status", summary, trace.get("outcome") or {})
    return {
        "problem": problem | {"uuid": uuid},
        "original": first_text("run_id", trace, summary) or uuid,
        "job": {
            "uuid": uuid,
            "title": statement_title(statement) or uuid,
            "status": status or "no_summary",
            # The pipeline needs the problem to continue.
            "imported": "resume"
            if statement and first_text("uuid", problem)
            else "view",
        },
    }


def restore_run(upload, user: str, page: str) -> str:
    """Add a run archive to the run list as a finished run; its results stay
    as they were produced and are not checked again. A resumed import uses
    the page's default models."""
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as staging:
        unpacked = Path(staging) / "run"
        try:
            with zipfile.ZipFile(upload) as archive:
                unpack_run(archive, unpacked)
            run = read_run(unpacked)
        # An archive is arbitrary data; any failure to read it is the upload's.
        except Exception as error:
            raise HTTPException(400, f"unreadable run archive: {error}")
        run_id = new_run_id("", f"imported_{run['original']}")
        dataset = write_problem(run_id, run["problem"])
        for item in unpacked.iterdir():
            shutil.move(item, RESULTS_DIR / run_id)
    now = time.time()
    store.add(
        {
            **run["job"],
            "run_id": run_id,
            "owner": user,
            "dataset": str(dataset),
            "state": "finished",
            "submitted": now,
            "ended": now,
            "agents": json.dumps(models.validate(PAGES[page].defaults)),
            "gpu_group": PAGES[page].gpu_groups[0],
            "page": page,
        }
    )
    return run_id


@app.post("/api/runs/import", status_code=201)
async def import_run(
    request: Request,
    user: str = Depends(current_user),
    page: str = DEFAULT_PAGE,
) -> dict:
    """Add a run from a zip of its folder or of its export (the body)."""
    page_of(page)
    if AUTH_REQUIRED and (
        sum(bool(job["imported"]) for job in store.jobs(user))
        >= MAX_IMPORTS_PER_USER
    ):
        raise HTTPException(
            429, f"at most {MAX_IMPORTS_PER_USER} imported runs per user"
        )
    TEMP_DIR.mkdir(exist_ok=True)
    size = 0
    with tempfile.TemporaryFile(dir=TEMP_DIR) as upload:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_IMPORT_BYTES:
                raise HTTPException(413, "run archive too large")
            try:
                await asyncio.to_thread(upload.write, chunk)
            except OSError as error:
                raise HTTPException(507, f"cannot store the upload: {error}")
        run_id = await asyncio.to_thread(restore_run, upload, user, page)
    return {"run_id": run_id, "state": "finished"}


def library_options(body: Submission, run_id: str) -> dict:
    """Run options for a submission's library and dependency."""
    options = {}
    if sum(map(bool, (body.library, body.library_text, body.new_library))) > 1:
        raise HTTPException(
            400, "give one of a library name, a library file or a new library"
        )
    if body.library:
        if (
            not LIBRARY_NAME.match(body.library)
            or not (LIBRARY_DIR / body.library).exists()
        ):
            raise HTTPException(400, f"unknown library {body.library}")
        options["library"] = f"libraries/{body.library}"
    elif body.library_text.strip():
        if len(body.library_text) > MAX_LIBRARY_CHARS:
            raise HTTPException(413, "library file too long")
        path = LIBRARY_DIR / "uploads" / f"{run_id}.lean"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.library_text)
        options["library"] = str(path.relative_to(HOME_DIR))
    elif body.new_library:
        # A new library starts empty; the run extends it with its result.
        name = body.new_library.removesuffix(".lean") + ".lean"
        path = LIBRARY_DIR / name
        if not LIBRARY_NAME.match(name):
            raise HTTPException(400, f"invalid library name {name}")
        if path.exists():
            raise HTTPException(400, f"library {name} exists; select it")
        if not body.extend_library:
            raise HTTPException(400, "a new library requires extend_library")
        options["library"] = f"libraries/{name}"
    if body.extend_library:
        if "library" not in options:
            raise HTTPException(400, "extend_library requires a library")
        options["extend_library"] = True
    if body.after:
        if store.get(body.after) is None:
            raise HTTPException(400, f"unknown run {body.after}")
        options["after"] = body.after
    if body.new_library:
        # Written once the submission is valid.
        libraries.resolve(options["library"]).write_text(FILE_HEADER)
    return options


@app.get("/api/libraries")
def list_libraries(user: str = Depends(current_user)) -> list[dict]:
    """Libraries in libraries/: name, size, and number of declarations."""
    return [
        {
            "name": path.name,
            "chars": len(text := path.read_text()),
            "declarations": len(libraries.declared_names(text)),
        }
        for path in sorted(LIBRARY_DIR.glob("*.lean"))
    ]


def resumable(job: dict) -> bool:
    """A cancelled run, or a finished one that neither proved nor disproved
    its theorem, with a trace to continue from. An imported run needs its
    problem and, having had no approval, a server without authentication; a
    page refuses the Claude roles it may not run."""
    ended = job["state"] == "cancelled" or (
        job["state"] == "finished" and job["status"] not in CONCLUSIVE
    )
    return (
        ended
        and "home" not in job
        and (run_dir(job, RESULTS_DIR) / "trace.json").exists()
        and (
            not job.get("imported")
            or (job["imported"] == "resume" and not AUTH_REQUIRED)
        )
        and claude_allowed(
            PAGES.get(job.get("page"), PAGES["dev"]), job_selection(job)
        )
    )


def read_json(path: Path) -> dict:
    """A JSON file; empty if it is missing or unreadable."""
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        logging.warning(f"{path} is not readable JSON; it is ignored")
        return {}


def problem_group(job: dict) -> str:
    """The group a run was given, else that of its problem id; empty if none."""
    if job.get("problem_group"):
        return job["problem_group"]
    groups = read_json(GROUPS).items()
    return next((name for p, name in groups if job["uuid"].startswith(p)), "")


def describe(job: dict) -> dict:
    result_dir = run_dir(job, RESULTS_DIR)
    benchmark = result_dir / "benchmark.json"
    costs = read_json(benchmark).get("costs", {}) if servable(job, benchmark) else {}
    files = [name for name in RESULT_PAGES if (result_dir / name).exists()]
    files += [
        name for name, pattern in ROOT_FILES.items()
        if any(result_dir.glob(pattern))
    ]
    files += [
        f"report{suffix}"
        for suffix in (".pdf", ".tex")
        if any((result_dir / "report").glob(f"*{suffix}"))
    ]
    files += [
        f"informal{suffix}"
        for suffix in (".pdf", ".tex")
        if any((result_dir / "informal").glob(f"*_informal{suffix}"))
    ]
    return {
        key: job.get(key)
        for key in (
            "run_id",
            "owner",
            "uuid",
            "title",
            "state",
            "status",
            "submitted",
            "started",
            "ended",
            "approved_by",
            "resumptions",
            "imported",
        )
    } | {
        "position": None if "home" in job else store.queue_position(job["run_id"]),
        "files": files,
        # An import's selection is for resuming; it did not produce the run.
        "agents": {} if job.get("imported") else job_selection(job),
        "gpu_group": job.get("gpu_group") or vista.DEFAULT_GROUP,
        "group": problem_group(job),
        "hidden": bool(job.get("hidden")),
        "resumable": resumable(job),
        "settled": job.get("status") in CONCLUSIVE,
        "cost_usd": costs.get("hosted_cost_usd"),
        "external": "home" in job,
    }


@app.get("/api/runs")
def list_runs(
    user: str = Depends(current_user), page: str = DEFAULT_PAGE
) -> list[dict]:
    """Every visible run on the development page; on a page with its own run
    list, the runs submitted from it."""
    own_runs = page_of(page).own_runs
    jobs = [
        job
        for job in store.jobs(visible_owner(user))
        if not own_runs or job.get("page") == page
    ]
    if page_of(page).user_servers:
        jobs += [
            job
            for account in other_users()
            if visible_owner(user) in (None, account)
            for job in user_jobs(account)
        ]
        jobs.sort(key=lambda job: job["submitted"] or 0, reverse=True)
    return [describe(job) for job in jobs]


@app.get("/api/runs/{run_id}")
def get_run(job: dict = Depends(owned_job)) -> dict:
    summary_path = run_dir(job, RESULTS_DIR) / "summary.json"
    summary = (
        json.loads(summary_path.read_text())
        if servable(job, summary_path)
        else None
    )
    return describe(job) | {"summary": summary}


@app.delete("/api/runs/{run_id}")
def cancel_run(job: dict = Depends(own_job)) -> dict:
    if not worker.cancel(job["run_id"]):
        raise HTTPException(409, f"run is {job['state']}")
    return describe(store.get(job["run_id"]))


@app.post("/api/runs/{run_id}/resume")
def resume_run(
    job: dict = Depends(own_job), user: str = Depends(current_user)
) -> dict:
    """Queue an ended run again; it continues from its last completed step."""
    if not resumable(job):
        raise HTTPException(409, f"run is {job['state']}; it cannot resume")
    check_capacity(user)
    if not worker.resume(job["run_id"]):
        raise HTTPException(409, "run changed state; reload the list")
    return describe(store.get(job["run_id"]))


@app.post("/api/runs/{run_id}/hide")
def hide_run(job: dict = Depends(own_job)) -> dict:
    """Leave a finished run out of the page's list; nothing is deleted."""
    if job["state"] not in TERMINAL_STATES:
        raise HTTPException(
            409, f"run is {job['state']}; only ended runs are hidden"
        )
    store.update(job["run_id"], hidden=1)
    return describe(store.get(job["run_id"]))


@app.post("/api/runs/{run_id}/unhide")
def unhide_run(job: dict = Depends(own_job)) -> dict:
    store.update(job["run_id"], hidden=0)
    return describe(store.get(job["run_id"]))


@app.post("/api/runs/{run_id}/approve")
def approve_run(run_id: str, user: str = Depends(approver)) -> dict:
    if not worker.approve(run_id, user):
        raise HTTPException(409, "run is not awaiting approval")
    return describe(store.get(run_id))


@app.post("/api/runs/{run_id}/reject")
def reject_run(run_id: str, user: str = Depends(approver)) -> dict:
    if not worker.reject(run_id, user):
        raise HTTPException(409, "run is not awaiting approval")
    return describe(store.get(run_id))


@app.get("/api/runs/{run_id}/files/{name}")
def get_file(name: str, job: dict = Depends(owned_job)) -> FileResponse:
    result_dir = run_dir(job, RESULTS_DIR)
    if name in ROOT_FILES:
        matches = sorted(result_dir.glob(ROOT_FILES[name]))
        path = matches[0] if matches else None
    elif name in ("report.pdf", "report.tex"):
        matches = sorted((result_dir / "report").glob(f"*{Path(name).suffix}"))
        path = matches[0] if matches else None
    elif name in ("informal.pdf", "informal.tex"):
        matches = sorted(
            (result_dir / "informal").glob(f"*_informal{Path(name).suffix}")
        )
        path = matches[0] if matches else None
    else:
        path = result_dir / name if name in RESULT_PAGES else None
    if path is None or not servable(job, path):
        raise HTTPException(404, "no such file")
    media_type = (
        "text/plain; charset=utf-8"
        if path.suffix in (".lean", ".tex", ".jsonl")
        else None
    )
    # Pages of imported runs come from other people: a sandbox keeps their
    # scripts from acting on this site as the viewer or saving files to the
    # viewer's machine. Pages rendered here may save their image exports.
    sandbox = "sandbox allow-scripts"
    if not job.get("imported"):
        sandbox += " allow-downloads"
    headers = (
        {"Content-Security-Policy": sandbox}
        if path.suffix == ".html"
        else None
    )
    return FileResponse(path, media_type=media_type, headers=headers)


# Export layout: the files a reader opens first at the root of the folder,
# everything else in subfolders by purpose.
EXPORT_FOLDERS = {
    "lean_modules": "Lean modules of the solution (Definitions, Theorems, Solutions, Check) and their build log",
    "report_build": "LaTeX by-products of the report and the informalization",
    "run_data": "Problem, summary and full trace of the run (JSON)",
    "logs": "Model calls and the orchestrator log",
}
STATUS_TEXT = {
    "proved": "proved; the Lean proof compiles and is verified",
    "disproved": "disproved; Lean accepts a proof of the statement's negation",
    "lemmas_unproved": "not proved; some lemmas have no Lean proof",
    "failed": "not proved; the run stopped before a proof",
}
# Pipeline role names as the page names them.
ROLE_NAMES = {pipeline: role for role, pipeline in models.ROLES.items()}


def model_label(spec: dict, version: str) -> str:
    """Readable model of an agent specification from a trace."""
    if (
        spec.get("backend") in ("aiprover", "openai_compatible")
        and spec.get("model") == "aiprover"
    ):
        return models.AIPROVER_VERSIONS.get(version, "AIProver")
    info = models.CLAUDE_MODELS.get(spec.get("model"))
    label = info["label"] if info else spec.get("model", "?")
    return (
        f"{label} ({spec['effort']} reasoning)" if spec.get("effort") else label
    )


def export_path(relative: Path) -> str | None:
    """Where a file of results/<run_id>/ goes in the export; None to leave it
    out."""
    name, suffix = relative.name, relative.suffix
    if relative.parts[0] in ("report", "informal"):
        return name if suffix in (".tex", ".pdf") else f"report_build/{name}"
    if (
        name.endswith(("_standalone.lean", RESULT_ROW_SUFFIX))
        or suffix == ".html"
    ):
        return name
    if suffix == ".lean" or name == "module_build.log":
        return f"lean_modules/{name}"
    return f"run_data/{relative}"


def restored_path(parts: tuple[str, ...]) -> Path | None:
    """Where a file of a run archive goes in results/<run_id>/: the inverse of
    `export_path`, which leaves a plain run folder as it is. None for logs,
    which are not restored, and the problem record, which an export writes
    anew."""
    folder, *rest = parts
    if folder == "logs" or parts == ("run_data", "problem.json"):
        return None
    if rest and folder in ("lean_modules", "run_data"):
        return Path(*rest)
    if rest and folder == "report_build":
        return Path("report", *rest)
    if not rest and folder.endswith((".tex", ".pdf")):
        return Path("informal" if "_informal." in folder else "report", folder)
    return Path(*parts)


def export_readme(record: dict, trace_agents: dict, files: list[str]) -> str:
    """A short guide to the exported folder; the models are those the trace
    records."""
    root_files = [name for name in files if "/" not in name]
    folders = [
        folder
        for folder in EXPORT_FOLDERS
        if any(name.startswith(folder + "/") for name in files)
    ]
    guide = {
        "_standalone.lean": "complete Lean 4 formalization and proof in one file (Mathlib)",
        RESULT_ROW_SUFFIX: "result as a row of the problem datasets (data/*.jsonl): "
        "the informalization's statement and proof, and the Lean source",
        "_informal.pdf": "informalization: the theorem and every lemma with its proof, "
        "in natural language without Lean",
        "_informal.tex": "LaTeX source of the informalization",
        ".pdf": "report: the formalized statement and proof in mathematical language, with the Lean code",
        ".tex": "LaTeX source of the report",
        "trace.html": "walkthrough of the run, step by step (open in a browser)",
        "trace_replay.html": "animated replay of the run (open in a browser)",
        "trace_graph.html": "graph of the run (open in a browser)",
    }
    lines = [
        f"# {record['title']}",
        "",
        f"Problem `{record['uuid']}`, run `{record['run_id']}`.",
        f"Result: {STATUS_TEXT.get(record['status'], record['status'] or record['state'])}.",
        "",
        "## Contents",
        "",
    ]
    for name in root_files:
        description = next(
            (text for key, text in guide.items() if name.endswith(key)), ""
        )
        lines.append(
            f"- `{name}`" + (f": {description}" if description else "")
        )
    lines += [f"- `{folder}/`: {EXPORT_FOLDERS[folder]}" for folder in folders]
    version = next(
        (
            choice["model"]
            for choice in record["agents"].values()
            if choice["provider"] == "aiprover"
        ),
        # An import's version is unknown; the trace names only "aiprover".
        None if record["imported"] else "trained",
    )
    roles = sorted(
        trace_agents,
        key=lambda name: (
            list(ROLE_NAMES).index(name)
            if name in ROLE_NAMES
            else len(ROLE_NAMES)
        ),
    )
    if roles:
        lines += ["", "## Models", ""]
        lines += [
            f"- {ROLE_NAMES.get(role, role)}: {model_label(trace_agents[role], version)}"
            for role in roles
        ]
    if any(name.endswith("_standalone.lean") for name in root_files):
        lines += [
            "",
            "The standalone file compiles with Lean v4.23.0 and Mathlib v4.23.0 in a Lake project:",
            "`lake env lean <file>`.",
        ]
    return "\n".join(lines) + "\n"


@app.get("/api/runs/{run_id}/export")
def export_run(job: dict = Depends(owned_job)) -> FileResponse:
    """The run as a zip of one folder: the standalone Lean file, the report
    (.tex, .pdf), the trace pages and a README at its root; Lean modules, LaTeX
    by-products, run data and logs in the subfolders of EXPORT_FOLDERS."""
    run_id = job.get("local_id", job["run_id"])
    dataset = Path(job["dataset"])
    rows = (
        (json.loads(line) for line in open(dataset) if line.strip())
        if servable(job, dataset, DATA_DIR)
        else ()
    )
    problem = next((row for row in rows if row["uuid"] == job["uuid"]), None)
    record = describe(job) | {
        "problem": {
            key: value
            for key, value in (problem or {}).items()
            if not key.startswith("_")
        }
    }
    entries = {
        "run_data/problem.json": json.dumps(
            record, indent=1, ensure_ascii=False
        )
    }
    sources = {}
    result_dir, log_dir = run_dir(job, RESULTS_DIR), run_dir(job, LOGS_DIR)
    for path in sorted(result_dir.rglob("*")) if result_dir.is_dir() else []:
        target = (
            export_path(path.relative_to(result_dir))
            if path.is_file() and servable(job, path)
            else None
        )
        if target:
            sources[target] = path
    for path in sorted(log_dir.rglob("*")) if log_dir.is_dir() else []:
        if path.is_file() and servable(job, path):
            sources[f"logs/{path.relative_to(log_dir)}"] = path
    # Root files first (Lean, PDF, TeX, pages), then the subfolders in order.
    order = lambda name: (
        "/" in name,
        list(EXPORT_FOLDERS).index(name.split("/")[0]) if "/" in name else 0,
        not name.endswith(".lean"),
        not name.endswith(".pdf"),
        not name.endswith(".tex"),
        "_informal." not in name,
        name,
    )
    sources = dict(sorted(sources.items(), key=lambda item: order(item[0])))
    trace_path = result_dir / "trace.json"
    try:
        trace = (
            json.loads(trace_path.read_text())
            if servable(job, trace_path)
            else {}
        )
        trace_agents = trace.get("agents") or {}
    except (OSError, json.JSONDecodeError):
        trace_agents = {}
    entries = {
        "README.md": export_readme(
            record, trace_agents, list(entries) + list(sources)
        ),
        **entries,
    }
    archive = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for target, text in entries.items():
            bundle.writestr(f"{run_id}/{target}", text)
        for target, path in sources.items():
            bundle.write(path, f"{run_id}/{target}")
    archive.close()
    return FileResponse(
        archive.name,
        media_type="application/zip",
        filename=f"{run_id}.zip",
        background=BackgroundTask(os.unlink, archive.name),
    )


@app.get("/api/runs/{run_id}/progress")
async def progress(job: dict = Depends(owned_job)) -> StreamingResponse:
    """Server-sent events: the run's progress log, line by line, then `end`."""
    log_path = run_dir(job, TEMP_DIR) / "progress.log"

    async def events():
        offset = 0
        while True:
            if servable(job, log_path):
                with open(log_path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                # A line still being written is sent once it is complete.
                complete = chunk[: chunk.rfind(b"\n") + 1]
                offset += len(complete)
                for line in complete.decode(errors="replace").splitlines():
                    yield f"data: {json.dumps(line)}\n\n"
            # A user's store may be briefly unreadable; the stream waits.
            state = (current_record(job) or {}).get("state")
            if state in TERMINAL_STATES:
                yield f"event: end\ndata: {json.dumps(state)}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
