"""HTTP access to the library.

    uvicorn aiprover_orchestration.library.api:app --port 8443

The query server (`server/app.py`) mounts `router` behind its token
authentication; the standalone `app` applies none and is meant for
localhost. Endpoints are synchronous, so FastAPI runs each Lean build in its
thread pool. The library is a dependency (`get_library`) so that tests can
replace it.
"""

from functools import lru_cache

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .verifier import Library, Rejected

router = APIRouter(prefix="/api/library", tags=["library"])


@lru_cache(maxsize=1)
def get_library() -> Library:
    return Library()


class Item(BaseModel):
    name: str
    lean: str
    title: str = ""
    statement_nl: str = ""
    source: str = ""
    tags: list[str] = []


class Submission(BaseModel):
    theorem_id: int
    content: str
    proof_type: str = "prove"
    explanation: str = ""


def found(record: dict | None, what: str) -> dict:
    if record is None:
        raise HTTPException(404, f"no such {what}")
    return record


def publish(kind: str, body: Item, library: Library) -> dict:
    try:
        return library.publish(kind, **body.model_dump())
    except Rejected as rejection:
        raise HTTPException(400, str(rejection))


def theorem(theorem_id: int, library: Library) -> dict:
    item = found(library.store.item(theorem_id), "theorem")
    if item["kind"] != "theorem":
        raise HTTPException(404, "no such theorem")
    return item


@router.get("/theorems")
def search(
    q: str = "",
    status: str = "",
    kind: str = "",
    library: Library = Depends(get_library),
) -> list[dict]:
    return library.store.search(q, status, kind)


@router.post("/theorems")
def add_theorem(body: Item, library: Library = Depends(get_library)) -> dict:
    return publish("theorem", body, library)


@router.post("/definitions")
def add_definition(body: Item, library: Library = Depends(get_library)) -> dict:
    return publish("definition", body, library)


@router.get("/theorems/{theorem_id}")
def get_theorem(
    theorem_id: int, library: Library = Depends(get_library)
) -> dict:
    return library.show(theorem(theorem_id, library)["name"])


@router.get("/theorems/{theorem_id}/graph")
def graph(theorem_id: int, library: Library = Depends(get_library)) -> dict:
    return library.store.graph(theorem(theorem_id, library)["id"])


@router.get("/theorems/{theorem_id}/open-leaves")
def open_leaves(
    theorem_id: int, library: Library = Depends(get_library)
) -> list[dict]:
    return library.store.open_leaves(theorem(theorem_id, library)["id"])


@router.post("/verify")
def verify(body: Submission, library: Library = Depends(get_library)) -> dict:
    theorem(body.theorem_id, library)
    try:
        return library.verify(
            body.theorem_id, body.content, body.proof_type, body.explanation
        )
    except Rejected as rejection:
        raise HTTPException(400, str(rejection))


@router.get("/submissions/{submission_id}")
def submission(
    submission_id: int, library: Library = Depends(get_library)
) -> dict:
    return found(library.store.submission(submission_id), "submission")


app = FastAPI(title="Theorem library")
app.include_router(router)
