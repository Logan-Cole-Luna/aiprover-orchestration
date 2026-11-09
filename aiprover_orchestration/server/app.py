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
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import AliasChoices, BaseModel, Field

from ..lean import releases
from ..library import api as library_api
from ..orchestrator import gpu_usage, libraries
from ..orchestrator.config import FILE_HEADER, current_names
from ..agents.base import TOKENS_PER_USD
from ..agents.codex_cli import DEFAULT_CODEX_MODEL
from ..orchestrator import project as project_file
from ..orchestrator.project import PROJECTS_DIR
from ..orchestrator.project_tasks import ROLES, THEOREM_NAME, task_dir
from ..orchestrator.reports import blueprint
from ..orchestrator.reports.informal import RESULT_ROW_SUFFIX
from ..orchestrator.trace import Trace
from ..utils.files import read_record
from ..utils.vocabulary import modernize
from ..paths import (
    CONFIG_DIR,
    DEFAULT_LEAN_RELEASE,
    HOME_DIR,
    LIBRARIES_DIR,
    LOGS_DIR,
    RESULTS_DIR,
    STATE_DIR,
    TEMP_DIR,
)

from . import accounts, model_servers, models, settings
from .jobs import (
    CONFIG,
    DATA_DIR,
    ROOT,
    DATABASE,
    OPEN_STATES,
    PLAN_REVIEW,
    PROBLEM_DIR,
    JobStore,
    Worker,
    endpoint_status,
    job_selection,
    trace_pages_command,
)
from .model_servers import control_master_running, run_command

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
MAX_SOURCE_BYTES = 2**28
MAX_TASK_USD = 200
MAX_IMPORTS_PER_USER = 20
# Libraries a run may build on (orchestrator/libraries.py); uploads are kept
# per run under uploads/.
LIBRARY_DIR = LIBRARIES_DIR
# A single-file library, or a package library (a folder).
LIBRARY_NAME = re.compile(r"^[A-Za-z0-9_-]+(?:\.lean)?$")
RESULT_PAGES = ("trace.html", "trace_replay.html", "summary.json")
# Single files of a run, by the name the page links them under.
ROOT_FILES = {
    "standalone.lean": "*_standalone.lean",
    "result.jsonl": f"*{RESULT_ROW_SUFFIX}",
    "blueprint.tex": "blueprint/content.tex",
    "blueprint.json": "blueprint/blueprint.json",
}
# Problem groups of this server, {"<start of a problem id>": "<group>"}. They
# are specific to its users, so they lie in its state and not in the repository.
GROUPS = STATE_DIR / "groups.json"
TERMINAL_STATES = ("finished", "cancelled", "rejected", PLAN_REVIEW)
# Statuses of a finished run that settle its theorem; such a run is final.
CONCLUSIVE = ("proved", "disproved")
# Statuses from which Resume does not continue a run.

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
    """Static files that a browser checks for changes on each load, except
    the vendored libraries, which change only with their pinned versions."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = (
            "public, max-age=604800"
            if path.startswith("vendor/")
            else "no-cache"
        )
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


# A per-user server offers the groups any account can reach.
PERSONAL_GROUPS = ("dgx", "cpu")
ALL_GROUPS = tuple(
    sorted(
        model_servers.GPU_GROUPS,
        key=lambda group: group != model_servers.DEFAULT_GROUP,
    )
)
PAGES = {
    "dev": Page(
        PERSONAL_GROUPS if PER_USER else ALL_GROUPS,
        user_servers=True,
    ),
    "user": Page(
        PERSONAL_GROUPS if PER_USER else ("dgx",),
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


@app.get("/api/me")
def me(request: Request, user: str = Depends(current_user)) -> dict:
    return {
        "user": user,
        "auth_required": AUTH_REQUIRED,
        "approver": AUTH_REQUIRED and token_user(request) is not None,
        # The Claude account of a per-user server, which is its user's own;
        # a shared server does not show its account.
        "per_user": PER_USER,
        "claude_account": accounts.account("claude") if PER_USER else None,
    }


def proxy_pool(group: model_servers.GpuGroup) -> list[dict]:
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
    for name, group in model_servers.GPU_GROUPS.items():
        if group.serverless:
            continue
        endpoint_up, endpoint_message = endpoint_status(
            bring_up=False, group=group
        )
        server_jobs = model_servers.server_job_details(group)
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
                for other in model_servers.GPU_GROUPS.values()
                if name in other.borrows
            ],
        }
    # A group whose own server is down is served while a server it
    # borrows answers.
    for name, group in model_servers.GPU_GROUPS.items():
        if group.serverless:
            continue
        lenders = [
            lender for lender in group.borrows if groups[lender]["endpoint_up"]
        ]
        if lenders and not groups[name]["endpoint_up"]:
            groups[name]["endpoint_up"] = True
            groups[name]["endpoint"] += f"; served by {', '.join(lenders)}"
    main = groups[model_servers.DEFAULT_GROUP]
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
    formal_statement: str = ""  # Lean, kept as given in theorem-proving mode
    orchestrator: Selection | None = None
    checker: Selection | None = None
    reviewer: Selection | None = None
    writer: Selection | None = None
    subagent: Selection | None = None
    escalation: Selection | None = None
    gpu_group: str = model_servers.DEFAULT_GROUP
    library: str = ""  # a file in libraries/
    library_text: str = ""  # or an uploaded library
    extend_library: bool = False
    new_library: str = ""  # or a library this run starts, in libraries/
    after: str = ""  # a run that must end first
    settings: dict = {}  # orchestrator settings (settings.SETTINGS)
    lean_release: str = ""  # an installed Lean release; by default the pin
    source_dir: str = ""  # a folder of data/ the orchestrator may read
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


def require_sign_in(selection: dict) -> None:
    """Refuse a selection whose service is not signed in, naming it for the
    page's prompt. A shared server's Claude is its owner's standing login."""
    chosen = {
        choice["provider"]
        for choice in selection.values()
    }
    for service, info in accounts.SERVICES.items():
        needed = service in chosen and (PER_USER or service == "codex")
        if needed and not accounts.account(service):
            raise HTTPException(
                409,
                {"sign_in": service, "message": f"Sign in to {info['label']}"},
            )


def check_group(name: str, selection: dict) -> None:
    """Refuse a GPU group that cannot serve the selection."""
    group = model_servers.GPU_GROUPS[name]
    if group.serverless and models.checkpoint_names(selection):
        raise HTTPException(
            400,
            f"group {name!r} has no model server: every role, "
            "including the subagent, must use Claude or a hosted model",
        )
    if not group.job_name and models.checkpoint_of(selection) is not None:
        # A persistent server serves its own version; a Vista checkpoint
        # needs a group with Vista server jobs.
        raise HTTPException(
            400,
            f"group {name!r} serves AIProver "
            f"{', '.join(sorted(models.PERSISTENT_VERSIONS))} only",
        )


def in_data(path: str) -> bool:
    """Whether a source lies in data/ or, as for imports, in users/."""
    roots = (PROJECTS_DIR.parent, USERS_DIR)
    return any(Path(path).resolve().is_relative_to(r.resolve()) for r in roots)


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
    release = body.lean_release or DEFAULT_LEAN_RELEASE
    if not releases.is_installed(release):
        raise HTTPException(400, f"Lean {release} is not installed here")
    if release != DEFAULT_LEAN_RELEASE:
        run_settings["lean_release"] = release
    if page.versions is not None and not (
        models.checkpoint_names(selection) <= set(page.versions)
    ):
        raise HTTPException(
            400, f"this page offers AIProver {', '.join(page.versions)} only"
        )
    check_group(body.gpu_group, selection)
    require_sign_in(selection)

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
        formal = body.formal_statement.strip()
        if not (statement or formal):
            raise HTTPException(
                400, "give a dataset uuid, an informal statement, or a formal one"
            )
        if formal and run_settings.get("mode") != "prove":
            raise HTTPException(400, "a formal statement is proved in mode prove")
        if max(len(statement), len(proof), len(formal)) > MAX_TEXT_CHARS:
            raise HTTPException(413, "statement or proof too long")
        run_id = new_run_id("user", body.name)
        uuid = f"user_{run_id}"
        row = {
            "uuid": uuid,
            "source_name": "user",
            "domain": "user",
            "informal_statement": statement,
            "informal_proof": proof or "(No informal proof is given.)",
            **({"formal_statement": formal} if formal else {}),
        }
        dataset = write_problem(run_id, row)
        title = statement_title(statement or formal)

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
        modernize(json.loads(summary_path.read_text())) if summary_path.exists() else {}
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
    # The run continues on the Lean release it began on.
    toolchain = (trace.get("lean_environment") or {}).get("toolchain", "")
    release = toolchain.rpartition(":")[2]
    other = release and release != DEFAULT_LEAN_RELEASE
    options = {"lean_release": release} if other else None
    return {
        "problem": problem | {"uuid": uuid},
        "original": first_text("run_id", trace, summary) or uuid,
        "job": {
            "uuid": uuid,
            "title": statement_title(statement) or uuid,
            "status": status or "no_summary",
            "options": json.dumps(options) if options else None,
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
            blueprint.from_summary(unpacked)
        # An archive is arbitrary data; any failure to read it is the upload's.
        except Exception as error:
            raise HTTPException(400, f"unreadable run archive: {error}")
        run_id = new_run_id("", f"imported_{run['original']}")
        dataset = write_problem(run_id, run["problem"])
        for item in unpacked.iterdir():
            shutil.move(item, RESULTS_DIR / run_id)
    # The trace's pages, which an archive of the results alone lacks.
    if (RESULTS_DIR / run_id / "trace.json").exists():
        run_command(trace_pages_command(RESULTS_DIR / run_id / "trace.json"), timeout=300)
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
        if libraries.is_package(options["library"]):
            built = libraries.release(options["library"])
            if built != (body.lean_release or DEFAULT_LEAN_RELEASE):
                raise HTTPException(
                    400, f"library {body.library} is built on Lean {built}"
                )
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
    if body.source_dir:
        if not in_data(body.source_dir):
            raise HTTPException(400, "the source must lie in data/")
        options["source_dir"] = body.source_dir
    if body.new_library:
        # Written once the submission is valid.
        libraries.resolve(options["library"]).write_text(FILE_HEADER)
    return options


@app.get("/api/libraries")
def list_libraries(user: str = Depends(current_user)) -> list[dict]:
    """Libraries in libraries/: single files and packages."""
    return [
        libraries.listing(path)
        for path in sorted(LIBRARY_DIR.iterdir())
        if (path.suffix == ".lean" and path.is_file())
        or (path.is_dir() and not path.name.startswith("."))
    ]


@app.post("/api/libraries", status_code=201)
async def upload_package(
    name: str,
    request: Request,
    lean_release: str = "",
    user: str = Depends(current_user),
) -> dict:
    """A package library from the body, a zip of Lean files with top-level
    root modules, built on Lean `lean_release` (by default the pin)."""
    release = lean_release or DEFAULT_LEAN_RELEASE
    if not releases.is_installed(release):
        raise HTTPException(400, f"Lean {release} is not installed here")
    body = await request.body()
    if len(body) > libraries.MAX_PACKAGE_BYTES:
        raise HTTPException(413, "library too large")
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise HTTPException(400, "a package library is uploaded as a zip")
    try:
        return await asyncio.to_thread(
            libraries.create_package, name, release, body
        )
    except ValueError as error:
        raise HTTPException(400, str(error))


def may_continue(job: dict) -> bool:
    """Whether this server may run the job further: a run of its own with a
    trace to continue from, whose Claude roles its page may run. An imported
    run needs its problem and, having had no approval, a server without
    authentication."""
    return (
        "home" not in job
        and (run_dir(job, RESULTS_DIR) / "trace.json").exists()
        and claude_allowed(
            PAGES.get(job.get("page"), PAGES["dev"]), job_selection(job)
        )
        and (
            not job.get("imported")
            or (job["imported"] == "resume" and not AUTH_REQUIRED)
        )
    )


def resumable(job: dict) -> bool:
    """An ended run without a theorem settled or a plan to decide, which can
    continue."""
    return (
        job["state"] in ("cancelled", "finished")
        and job["status"] not in CONCLUSIVE
        and may_continue(job)
    )


def read_json(path: Path) -> dict:
    """A JSON file; empty if it is missing or unreadable."""
    try:
        return modernize(json.loads(path.read_text())) if path.exists() else {}
    except (OSError, ValueError):
        logging.warning(f"{path} is not readable JSON; it is ignored")
        return {}


def problem_group(job: dict) -> str:
    """The group a run was given, else that of its problem id; empty if none."""
    if job.get("problem_group"):
        return job["problem_group"]
    groups = read_json(GROUPS).items()
    return next((name for p, name in groups if job["uuid"].startswith(p)), "")


def reportable(job: dict, files: list[str]) -> bool:
    """A settled run of this server without its informal proof or blueprint."""
    return (
        job["state"] == "finished"
        and job["status"] in CONCLUSIVE
        and not job.get("imported")
        and (
            "informal.pdf" not in files
            or (job["status"] == "proved" and "blueprint.tex" not in files)
        )
        and may_continue(job)
    )


def spent_limit(job: dict) -> bool:
    """Whether a run has spent its hosted-model limit, and so would stop at once.
    The final review of a verified proof is exempt."""
    result_dir = run_dir(job, RESULTS_DIR)
    paths = result_dir / "summary.json", result_dir / "benchmark.json"
    if job["status"] == "review_flagged" or not all(
        servable(job, path) for path in paths
    ):
        return False
    limit = current_names(read_json(paths[0]).get("config", {})).get(
        "max_hosted_usd"
    )
    costs = read_json(paths[1]).get("costs", {})
    cost = (costs.get("hosted_cost_usd") or 0) + (
        costs.get("unpriced_tokens") or 0
    ) / TOKENS_PER_USD
    return bool(limit and cost and cost >= limit)


def run_files(result_dir: Path) -> list[str]:
    """The names under which the page links a run's files."""
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
    return files


@lru_cache(maxsize=1024)
def recorded_stop(path: Path, stamp: float) -> str | None:
    """Why a run stopped, from its summary written at `stamp`."""
    return read_json(path).get("error_kind")


def stop_of(job: dict, result_dir: Path) -> str | None:
    path = result_dir / "summary.json"
    if job.get("status") != "failed" or not servable(job, path):
        return None
    return recorded_stop(path, gpu_usage.modified(path))


def describe(job: dict) -> dict:
    result_dir = run_dir(job, RESULTS_DIR)
    benchmark = result_dir / "benchmark.json"
    costs = read_json(benchmark).get("costs", {}) if servable(job, benchmark) else {}
    gpu = (
        gpu_usage.usage_of(result_dir)
        if servable(job, result_dir / gpu_usage.USAGE_FILE)
        else {}
    )
    files = run_files(result_dir)
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
        "gpu_group": job.get("gpu_group") or model_servers.DEFAULT_GROUP,
        "group": problem_group(job),
        "hidden": bool(job.get("hidden")),
        "resumable": resumable(job),
        "stop": stop_of(job, result_dir),
        "reportable": reportable(job, files),
        "plan_review": job["state"] == PLAN_REVIEW,
        "settled": job.get("status") in CONCLUSIVE,
        "cost_usd": costs.get("hosted_cost_usd"),
        "tokens": costs.get("hosted_tokens"),
        "gpu": gpu,
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
        modernize(json.loads(summary_path.read_text()))
        if servable(job, summary_path)
        else None
    )
    return describe(job) | {"summary": summary}


@app.delete("/api/runs/{run_id}")
def cancel_run(job: dict = Depends(own_job)) -> dict:
    if not worker.cancel(job["run_id"]):
        raise HTTPException(409, f"run is {job['state']}")
    return describe(store.get(job["run_id"]))


def set_options(job: dict, **values) -> None:
    """Add settings to the job's options, which its next start applies."""
    options = modernize(json.loads(job.get("options") or "{}")) | values
    store.update(job["run_id"], options=json.dumps(options))


def requeued(job: dict) -> dict:
    """Queue an ended run again."""
    if not worker.resume(job["run_id"]):
        raise HTTPException(409, "run changed state; reload the list")
    return describe(store.get(job["run_id"]))




class Resumption(BaseModel):
    max_hosted_usd: int | None = Field(
        None, validation_alias=AliasChoices("max_hosted_usd", "max_claude_usd")
    )
    orchestrator: Selection | None = None
    gpu_group: str = ""
    # A research report for a replan; one command-line argument of the run.
    findings: str = Field("", max_length=60000)


def resumption_settings(body: Resumption, job: dict) -> dict:
    """The job's fields and options for a resumption with new choices. A run
    keeps its reference material, which only its trace names for an import."""
    fields, options = {}, {}
    trace = read_record(run_dir(job, RESULTS_DIR) / "trace.json")
    agents = trace.get("config", {}).get("agents", {})
    source = agents.get("orchestrator", {}).get("source_dir")
    if source and Path(source).is_dir() and in_data(source):
        options["source_dir"] = source
    page = PAGES.get(job.get("page"), PAGES[DEFAULT_PAGE])
    try:
        # A run from before model choices keeps the served config.
        selection = job_selection(job)
        selection = selection and models.validate(selection)
        if body.max_hosted_usd is not None:
            limit = {"max_hosted_usd": body.max_hosted_usd}
            options |= settings.validate(limit)
        if body.orchestrator:
            choice = {
                "orchestrator": body.orchestrator.model_dump(exclude_none=True)
            }
            selection = models.validate(selection | choice)
            fields["agents"] = json.dumps(selection)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not claude_allowed(page, selection):
        raise HTTPException(400, "this page runs no Claude role")
    if body.gpu_group:
        if body.gpu_group not in page.gpu_groups:
            raise HTTPException(422, f"unknown GPU group {body.gpu_group}")
        check_group(body.gpu_group, selection)
        fields["gpu_group"] = body.gpu_group
    require_sign_in(selection)
    if body.findings.strip():
        recorded = modernize(json.loads(job.get("options") or "{}"))
        revisions = [
            step.get("revision", 0)
            for step in trace.get("steps", [])
            if step.get("event") == "plan_revised"
        ]
        options["sketch_feedback"] = body.findings.strip()
        options["plan_revision"] = (
            max(revisions + [recorded.get("plan_revision", 0)]) + 1
        )
    return fields | {"options": options}


@app.post("/api/runs/{run_id}/resume")
def resume_run(
    body: Resumption = Resumption(),
    job: dict = Depends(own_job),
    user: str = Depends(current_user),
) -> dict:
    """Queue an ended run again; it continues from its last completed step.
    A run that has spent its hosted-model limit needs a higher one."""
    if not resumable(job):
        raise HTTPException(409, f"run is {job['state']}; it cannot resume")
    check_capacity(user)
    if body.max_hosted_usd is None and spent_limit(job):
        raise HTTPException(409, "spending limit reached")
    fields = resumption_settings(body, job)
    set_options(job, **fields.pop("options"))
    if fields:
        store.update(job["run_id"], **fields)
    return requeued(job)


@app.post("/api/runs/{run_id}/report")
def request_report(
    job: dict = Depends(own_job), user: str = Depends(current_user)
) -> dict:
    """Queue a settled run to write its documents: resumed, it restores its
    proof and review from the trace, and needs no model server."""
    if not reportable(job, run_files(run_dir(job, RESULTS_DIR))):
        raise HTTPException(409, "this run has no report to write")
    check_capacity(user)
    set_options(job, write_reports=True, documents_only=True)
    return requeued(job)


class PlanDecision(BaseModel):
    decision: Literal["approve", "comment", "revise", "reject"]
    comments: str = Field("", max_length=20000)


@app.post("/api/runs/{run_id}/plan")
def decide_plan(
    body: PlanDecision,
    job: dict = Depends(own_job),
    user: str = Depends(current_user),
) -> dict:
    """Record the reviewer's decision on a proof plan. Each decision but a
    rejection queues the run, which continues from its trace."""
    if job["state"] != PLAN_REVIEW or not may_continue(job):
        raise HTTPException(409, "this run is not awaiting review of its plan")
    comments = body.comments.strip()
    if body.decision in ("comment", "revise") and not comments:
        raise HTTPException(422, "this decision requires comments")
    if body.decision == "reject":
        store.update(job["run_id"], state="rejected", approved_by=user)
        return describe(store.get(job["run_id"]))
    check_capacity(user)
    options = modernize(json.loads(job.get("options") or "{}"))
    if body.decision == "revise":
        set_options(
            job,
            sketch_feedback=comments,
            plan_revision=options.get("plan_revision", 0) + 1,
        )
    else:
        guidance = comments if body.decision == "comment" else ""
        set_options(
            job,
            sketch_approval=False,
            sketch_guidance=guidance,
            sketch_feedback="",
        )
    store.update(job["run_id"], approved_by=user)
    return requeued(job)


def project_member(member: dict, account: str = "") -> dict:
    """A project's member with its run and the lemmas of its plan."""
    run = member.get("run")
    job = find_job(f"{account}:{run}" if account else run) if run else None
    if job is None:
        return member | {"job": None, "lemmas": [0, 0]}
    path = run_dir(job, RESULTS_DIR) / "summary.json"
    summary = read_record(path) if servable(job, path) else {}
    lemmas = (summary.get("sketch") or {}).get("lemmas", [])
    proved = sum(bool(lemma.get("proved")) for lemma in lemmas)
    return member | {"job": describe(job), "lemmas": [proved, len(lemmas)]}


def task_state(task: dict) -> str:
    """A task's state; one whose process died has failed."""
    running = task["state"] == "running"
    if running and not Path(f"/proc/{task['pid']}").exists():
        return "failed"
    return task["state"]


def task_view(path: Path, task: dict) -> dict:
    report = path.with_suffix("") / "tasks" / task["id"] / "report.md"
    readable = report.exists() and not report.is_symlink()
    text = report.read_text(errors="replace") if readable else ""
    return task | {"state": task_state(task), "report": text}


def source_files(project: dict, home: Path = HOME_DIR) -> list[str]:
    """The source files of a project of the state folder `home`."""
    root = project.get("source") or "/nonexistent"
    if not Path(root).resolve().is_relative_to(home.resolve()):
        return []
    return sorted(
        os.path.relpath(os.path.join(folder, file), root)
        for folder, _, files in os.walk(root, followlinks=True)
        for file in files
    )


def project_view(path: Path, account: str = "") -> dict:
    """A project; one of an account's server is named `<account>:<name>` and
    can only be read, like the runs of that server."""
    project = project_file.load(path.stem, path.parent)
    members = [project_member(m, account) for m in project["members"]]
    tasks = [task_view(path, task) for task in project["tasks"]]
    spent = sum(m["job"]["cost_usd"] or 0 for m in members if m["job"])
    research = project.get("research_usd", 0) + sum(
        task.get("cost_usd", 0) for task in tasks
    )
    return project | {
        "members": members,
        "tasks": tasks,
        "research_usd": round(research, 2),
        "paper": (path.parent / path.stem / f"{path.stem}_paper.pdf").exists(),
        "source_files": source_files(project, path.parents[2]),
        "spent_usd": round(spent + research, 2),
        "created": project.get("created", path.stat().st_mtime),
    } | ({"name": f"{account}:{path.stem}", "owner": account} if account else {})


class ModelServer(BaseModel):
    name: str
    url: str = Field(pattern=r"^https?://")
    api_key: str = ""
    model: str = ""
    kind: Literal["aiprover", "general", "hosted"] = "general"


def server_admin(request: Request) -> str:
    """Whoever may send prompts to a new address: a token's holder, or a
    connection from this machine, which a per-user server's tunnel is."""
    if request.client.host in ("127.0.0.1", "::1"):
        return current_user(request)
    return approver(request)


def served_models(url: str, api_key: str) -> list[str]:
    """The model ids a server reports, which also shows that it answers."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    call = urllib.request.Request(f"{url}/models", headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=15) as reply:
            return [entry["id"] for entry in json.load(reply)["data"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"{url}/models: {error}") from error


class Secret(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


Service = Literal["claude", "codex"]


@app.exception_handler(accounts.AccountError)
async def refuse_sign_in(request: Request, error: accounts.AccountError):
    return JSONResponse({"detail": str(error)}, 400)


@app.get("/api/accounts/{service}")
def read_account(service: Service, user: str = Depends(current_user)) -> dict:
    return {"account": accounts.account(service, fresh=True)}


@app.post("/api/accounts/{service}/sign-in")
def start_sign_in(service: Service, user: str = Depends(server_admin)) -> dict:
    """Start the service's own sign-in; the page shows where it continues."""
    return accounts.start(service)


@app.post("/api/accounts/claude/code")
def finish_sign_in(body: Secret, user: str = Depends(server_admin)) -> dict:
    accounts.finish(body.value)
    return {"account": accounts.account("claude")}


@app.post("/api/accounts/{service}/api-key")
def set_api_key(
    service: Service, body: Secret, user: str = Depends(server_admin)
) -> dict:
    accounts.set_api_key(service, body.value)
    return {"account": accounts.account(service)}


@app.post("/api/model-servers", status_code=201)
def add_model_server(
    body: ModelServer, user: str = Depends(server_admin)
) -> dict:
    """Add a server after checking that it answers."""
    if not model_servers.SERVER_NAME.fullmatch(body.name):
        raise HTTPException(400, "a name has letters, digits, - and _")
    url = body.url.rstrip("/")
    served = served_models(url, body.api_key)
    model = body.model or next(iter(served), "")
    if model not in served:
        raise HTTPException(
            400, f"the server offers {', '.join(served) or 'no model'}"
        )
    server = {"url": url, "model": model, "kind": body.kind}
    model_servers.add_custom_server(body.name, server, body.api_key)
    return {"name": body.name, "model": model}


@app.delete("/api/model-servers/{name}")
def remove_model_server(name: str, user: str = Depends(server_admin)) -> dict:
    if name not in model_servers.custom_servers():
        raise HTTPException(404, f"unknown model server {name}")
    server = {"provider": "custom", "model": name, "effort": ""}
    for job in store.in_states(*OPEN_STATES):
        if server in job_selection(job).values():
            raise HTTPException(409, f"an open run uses {name}")
    model_servers.remove_custom_server(name)
    return {"removed": name}


@app.get("/api/projects")
def list_projects(
    user: str = Depends(current_user), page: Page = Depends(page_of)
) -> list[dict]:
    if page.own_runs and not PER_USER:
        return []
    projects = [project_view(p) for p in sorted(PROJECTS_DIR.glob("*.json"))]
    for account in other_users():
        folder = USERS_DIR / account / "data" / "projects"
        projects += [
            project_view(path, account)
            for path in sorted(folder.glob("*.json"))
        ]
    return sorted(projects, key=lambda project: project["created"], reverse=True)


class NewProject(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field("", max_length=MAX_TEXT_CHARS)
    gpu_group: str
    lean_release: str = ""
    subagent: Selection | None = None
    service: Service = "claude"  # of the orchestrator and its reviewers
    # A library in libraries/ (a file or a package) instead of a new file.
    library: str = ""


@app.post("/api/projects", status_code=201)
def create_project(
    body: NewProject,
    user: str = Depends(current_user),
    page: Page = Depends(page_of),
) -> dict:
    """A project on a new library, named after its title."""
    if page.own_runs and not PER_USER:
        raise HTTPException(403, "this page has no projects")
    name = re.sub(r"[^a-z0-9]+", "_", body.title.lower()).strip("_")
    library = LIBRARY_DIR / (body.library or f"{name}.lean")
    if not name or (PROJECTS_DIR / f"{name}.json").exists():
        raise HTTPException(400, f"project {name} exists")
    if body.library and not (LIBRARY_NAME.match(body.library) and library.exists()):
        raise HTTPException(400, f"unknown library {body.library}")
    if not body.library and library.exists():
        raise HTTPException(400, f"library {library.name} exists; select it")
    release = body.lean_release
    if body.library and libraries.is_package(f"libraries/{body.library}"):
        # The project's runs build on the release the package was built on.
        release = libraries.release(f"libraries/{body.library}")
        if body.lean_release and body.lean_release != release:
            raise HTTPException(
                400, f"library {body.library} is built on Lean {release}"
            )
        if release == DEFAULT_LEAN_RELEASE:
            release = ""
    if body.gpu_group not in page.gpu_groups:
        raise HTTPException(400, f"unknown GPU group {body.gpu_group!r}")
    if release and not releases.is_installed(release):
        raise HTTPException(400, f"Lean {release} is not installed")
    selection = {"lean_release": release} if release else {}
    if body.subagent:
        selection["subagent"] = body.subagent.model_dump(exclude_none=True)
        check_group(
            body.gpu_group, models.validate({"subagent": selection["subagent"]})
        )
    if body.service == "codex":
        choice = {
            "provider": "codex",
            "model": DEFAULT_CODEX_MODEL,
            "effort": "high",
        }
        for role in ("orchestrator", "checker", "reviewer", "writer"):
            selection[role] = choice
    LIBRARY_DIR.mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not body.library:
        library.write_text(FILE_HEADER)
    project_file.save({
        "name": name,
        "title": body.title,
        "goal": body.goal,
        "library": library.name,
        "gpu_group": body.gpu_group,
        "service": body.service,
        "selection": selection,
        "members": [],
        "created": time.time(),
    })
    return {"name": name}


def known_project(
    name: str,
    user: str = Depends(current_user),
    page: Page = Depends(page_of),
) -> str:
    if (
        (page.own_runs and not PER_USER)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
        or not (PROJECTS_DIR / f"{name}.json").exists()
    ):
        raise HTTPException(404, f"unknown project {name}")
    return name


def project_path(
    name: str,
    user: str = Depends(current_user),
    page: Page = Depends(page_of),
) -> Path:
    """The file of a project this page may read."""
    account, separator, local = name.rpartition(":")
    if not separator:
        return PROJECTS_DIR / f"{known_project(name, user, page)}.json"
    path = USERS_DIR / account / "data" / "projects" / f"{local}.json"
    if account not in other_users() or not path.exists():
        raise HTTPException(404, f"unknown project {name}")
    return path


@app.post("/api/projects/{name}/source", status_code=201)
async def upload_source(
    file: str, request: Request, name: str = Depends(known_project)
) -> dict:
    """Add the body, a paper or a zip of its files, to the project's source."""
    body = await request.body()
    if len(body) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "source too large")
    return await asyncio.to_thread(store_source, name, file, body)


def store_source(name: str, file: str, body: bytes) -> dict:
    target = PROJECTS_DIR / name / "source"
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(io.BytesIO(body)):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise HTTPException(413, "source unpacks to too much data")
            archive.extractall(target)
    else:
        (target / Path(file).name).write_bytes(body)
    project_file.update(
        name, lambda project: project.update(source=str(target))
    )
    return {"source": str(target)}


class Member(BaseModel):
    uuid: str = Field(pattern=rf"^{THEOREM_NAME.pattern}$")
    role: Literal[ROLES] = "main"
    statement: str = Field("", max_length=MAX_TEXT_CHARS)
    proof: str = Field("", max_length=MAX_TEXT_CHARS)
    formal_statement: str = Field("", max_length=MAX_TEXT_CHARS)
    uses: list[str] = []


@app.put("/api/projects/{name}/members")
def set_members(
    body: list[Member], name: str = Depends(known_project)
) -> dict:
    """Replace the members without a run: the plan the user edits."""
    def change(project: dict) -> None:
        try:
            project_file.replace_unstarted(project, [m.model_dump() for m in body])
        except ValueError as error:
            raise HTTPException(400, str(error))

    project_file.update(name, change)
    return {"members": len(body)}


class Task(BaseModel):
    kind: Literal["research", "check", "paper"]
    question: str = Field("", max_length=MAX_TEXT_CHARS)
    max_usd: float = Field(20, gt=0, le=MAX_TASK_USD)
    blueprint: bool = False  # the report's theorems replace the unstarted ones


def task_user(request: Request) -> str:
    """Tasks spend without an approval, so they need one's token."""
    return approver(request) if AUTH_REQUIRED else current_user(request)


def project_lean(project: dict) -> dict[str, str]:
    """The Lean of a project's tasks: its runs' release, and its library as
    their workspace if the library is a package."""
    environment = {}
    release = (project.get("selection") or {}).get("lean_release")
    if release and release != DEFAULT_LEAN_RELEASE:
        environment["AIPROVER_LEAN_RELEASE"] = release
    library = f"libraries/{project.get('library', '')}"
    if project.get("library") and libraries.is_package(library):
        environment["AIPROVER_LIBRARY_PACKAGE"] = str(libraries.resolve(library))
    return environment


@app.post("/api/projects/{name}/tasks", status_code=201)
def start_task(
    body: Task,
    name: str = Depends(known_project),
    user: str = Depends(task_user),
) -> dict:
    """Start a task on the project's service, one at a time."""
    project_service = project_file.load(name).get("service", "claude")
    # A paper's writer is Claude on every project.
    service = "claude" if body.kind == "paper" else project_service
    require_sign_in({"orchestrator": {"provider": service}})
    task_id = time.strftime("%Y%m%d_%H%M%S")
    task = body.model_dump() | {
        "id": task_id, "state": "running", "started": time.time(),
    }

    def change(project: dict) -> None:
        if any(task_state(other) == "running" for other in project["tasks"]):
            raise HTTPException(409, "a task of this project is running")
        folder = task_dir(name, task_id)
        folder.mkdir(parents=True)
        task["pid"] = subprocess.Popen(
            [sys.executable, "-m",
             "aiprover_orchestration.orchestrator.project_tasks",
             name, task_id],
            cwd=folder,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                **accounts.environment(),
                **project_lean(project),
                "PYTHONPATH": str(ROOT),
            },
            start_new_session=True,
        ).pid
        project["tasks"].append(task)

    project_file.update(name, change)
    return task


class Decision(BaseModel):
    decision: Literal["confirmed", "rejected"]
    note: str = Field("", max_length=4000)


@app.post("/api/projects/{name}/reviews/{index}")
def decide_review(
    index: int,
    body: Decision,
    name: str = Depends(known_project),
    user: str = Depends(current_user),
) -> dict:
    """Record a person's decision on a claim."""
    def change(project: dict) -> None:
        if not 0 <= index < len(project["reviews"]):
            raise HTTPException(404, "unknown claim")
        project["reviews"][index] |= body.model_dump() | {"by": user}

    project_file.update(name, change)
    return {"decision": body.decision}


@app.get("/api/projects/{name}/blueprint")
def project_blueprint(path: Path = Depends(project_path)) -> dict:
    """The project's results as a plan whose lemmas are its members."""
    account = path.parents[2].name if path.is_relative_to(USERS_DIR) else ""
    project = project_file.load(path.stem, path.parent)
    members = [project_member(m, account) for m in project["members"]]
    known = {member["uuid"] for member in members}
    complete = all(member.get("statement") for member in members)
    rows = {} if complete else read_dataset()

    def node(member: dict) -> dict:
        uuid, job = member["uuid"], member["job"]
        proved, total = member["lemmas"]
        settled = job and job["status"] in ("proved", "verified_in_lean")
        row = rows.get(uuid, {})
        return {
            "name": uuid,
            "lean": "",
            "statement": member.get("statement")
            or row.get("informal_statement", ""),
            "idea": f"{proved} of {total} lemmas proved" if total else "",
            "uses": [used for used in member.get("uses", []) if used in known],
            "status": "proved" if settled else "planned",
            "run": job["run_id"] if job else "",
        }

    return {"version": 1, "nodes": [*map(node, members)], "problems": []}


@app.get("/api/projects/{name}/library")
def project_library(path: Path = Depends(project_path)) -> FileResponse:
    library = project_file.load(path.stem, path.parent)["library"]
    if not LIBRARY_NAME.match(library):
        raise HTTPException(404, "unknown library")
    target = path.parents[2] / "libraries" / library
    # A package is shown by its table of contents, one line per declaration.
    if target.is_dir():
        target = target / "views" / "toc.md"
    return FileResponse(target, media_type="text/plain; charset=utf-8")


@app.get("/api/projects/{name}/source/{path:path}")
def project_source(
    path: str, project_file_path: Path = Depends(project_path)
) -> FileResponse:
    project = project_file.load(project_file_path.stem, project_file_path.parent)
    if path not in source_files(project, project_file_path.parents[2]):
        raise HTTPException(404, "unknown file")
    pdf = path.endswith(".pdf")
    return FileResponse(
        Path(project["source"]) / path,
        media_type="application/pdf" if pdf else "text/plain; charset=utf-8",
    )


@app.get("/api/projects/{name}/paper.{ext}")
def project_paper(
    ext: Literal["pdf", "tex"], path: Path = Depends(project_path)
) -> FileResponse:
    paper = path.parent / path.stem / f"{path.stem}_paper.{ext}"
    if not paper.exists():
        raise HTTPException(404, "no paper yet")
    text = "text/plain; charset=utf-8" if ext == "tex" else None
    return FileResponse(paper, media_type=text)


@app.post("/api/projects/{name}/launch")
def launch_project(
    name: str = Depends(known_project),
    user: str = Depends(current_user),
    page: str = DEFAULT_PAGE,
) -> dict:
    """Queue the members without a run, each after the one before it."""
    def start(project: dict) -> list[str]:
        return project_file.launch(
            project,
            lambda body: submit(Submission(**body | {"page": page}), user),
        )

    return {"started": project_file.update(name, start)}


@lru_cache(maxsize=32)
def cached_metrics(result_dir: Path, stamp: tuple) -> dict:
    """`run_metrics` of a run whose files have the times `stamp`: a large
    trace takes seconds to parse."""
    return blueprint.run_metrics(result_dir)


@app.get("/api/runs/{run_id}/metrics")
def get_metrics(job: dict = Depends(owned_job)) -> dict:
    """Verification, size, effort and cost of a run."""
    result_dir = run_dir(job, RESULTS_DIR)
    names = ("trace.json", "benchmark.json")
    paths = [result_dir / name for name in names]
    paths += result_dir.glob("*_standalone.lean")
    paths = [path for path in paths if path.exists()]
    if not servable(job, result_dir / "summary.json") or not all(
        servable(job, path) for path in paths
    ):
        raise HTTPException(404, "no summary")
    return cached_metrics(
        result_dir, tuple(gpu_usage.modified(path) for path in paths)
    )


# Tags of the Lean packages, which change rarely: (time read, tag sets).
release_tags = (float("-inf"), dict.fromkeys(releases.PACKAGES, frozenset()))


def package_tags() -> dict:
    """The package tags, read at most hourly. A failed read keeps the last
    ones, so that the page still lists the releases."""
    global release_tags
    read, found = release_tags
    if time.monotonic() - read > 3600:
        try:
            found = {
                name: releases.tags(url)
                for name, url in releases.PACKAGES.items()
            }
        except (OSError, subprocess.SubprocessError) as error:
            logging.warning(f"Lean package tags not read: {error}")
        release_tags = (time.monotonic(), found)
    return found


@app.get("/api/lean-releases")
def lean_releases(user: str = Depends(current_user)) -> dict:
    """The Lean releases of this server, and those it can install."""
    offers = releases.offers(package_tags())
    versions = offers.keys() | releases.installed_releases()
    return {
        "default": DEFAULT_LEAN_RELEASE,
        "free_gb": releases.free_gb(),
        "needed_gb": releases.NEEDED_GB,
        "releases": [
            {
                "release": release,
                "state": releases.state(release),
                "packages": offers.get(release),
            }
            for release in sorted(versions, key=releases.key, reverse=True)
        ],
    }


@app.post("/api/lean-releases/{release}/install")
def install_lean_release(
    release: str, user: str = Depends(current_user)
) -> dict:
    """Start the download of a Lean release with its matching packages."""
    if not releases.matching_tags(release, package_tags()):
        raise HTTPException(404, f"{release} is not available")
    if releases.is_installed(release):
        raise HTTPException(409, f"{release} is installed")
    free = releases.free_gb()
    if free < releases.NEEDED_GB:
        raise HTTPException(
            507,
            f"{release} requires about {releases.NEEDED_GB} GB of disk space, "
            f"and {free} GB are free",
        )
    if not releases.start_install(release):
        raise HTTPException(409, "another Lean release is being installed")
    return {"release": release}


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
    PLAN_REVIEW: "awaiting review of the proof plan",
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


def export_readme(
    record: dict,
    trace_agents: dict,
    files: list[str],
    release: str,
) -> str:
    """A short guide to the exported folder; the models and Lean `release`
    are those the trace records."""
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
            f"The standalone file compiles with Lean {release} and Mathlib {release} in a Lake project:",
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
            modernize(json.loads(trace_path.read_text()))
            if servable(job, trace_path)
            else {}
        )
    except (OSError, json.JSONDecodeError):
        trace = {}
    trace_agents = trace.get("agents") or {}
    toolchain = (trace.get("lean_environment") or {}).get("toolchain", "")
    entries = {
        "README.md": export_readme(
            record,
            trace_agents,
            list(entries) + list(sources),
            toolchain.rpartition(":")[2] or DEFAULT_LEAN_RELEASE,
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
    log_path = run_dir(job, LOGS_DIR) / "run.log"

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
