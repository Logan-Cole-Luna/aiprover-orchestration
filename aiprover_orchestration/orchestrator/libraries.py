r"""Libraries of verified results that runs build on.

A library is a Lean file that compiles on its own: definitions and theorems
with complete proofs (no `sorry`). A run given a library formalizes only its
new statement against the library's definitions (it adds definitions only
where the library lacks them), and its provers may use the library's
theorems. The library text is the prefix of the run's definitions, so every
file the run checks, and its final solution, compiles without the library
being installed anywhere.

A verified run extends its library with what it added: its new definitions,
its lemmas, and its main proof under the target theorem's name in place of
`solution`. The extension is appended under a file lock and kept only if the
extended library compiles without `sorry`.

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Similar] Libraries that grow with proved results: LEGO-Prover (Wang et
  al., ICLR 2024), https://github.com/wiio12/LEGO-Prover; LeanAgent
  (Kumarappan et al., ICLR 2025), https://github.com/lean-dojo/LeanAgent

A library may also be a Lake package, `libraries/<name>/`, built against a
Lean release's Mathlib: a run imports its root modules instead of repeating
its text, and Lean finds its build through `LEAN_PATH`. Agents read it through
generated views: per module its statements with the proofs elided, and a table
of contents with one line per declaration.

    python3 -m aiprover_orchestration.orchestrator.libraries add \
        libraries/<name>.lean results/<run_id>
    python3 -m aiprover_orchestration.orchestrator.libraries views \
        libraries/<name>
"""

import fcntl
import json
import io
import re
import shutil
import sys
import zipfile
from pathlib import PurePosixPath
from pathlib import Path

from ..utils.vocabulary import modernize
from ..lean.checker import (
    LeanChecker,
    build_lock,
    build_locked,
    executable_constructs,
    rejected,
)
from ..lean.text import (
    declaration_names,
    declarations,
    drop_imports,
    statements_only,
)
from ..paths import (
    DEFAULT_LEAN_RELEASE,
    HOME_DIR,
    LIBRARIES_DIR,
    RELEASES_DIR,
    TEMP_DIR,
    WORKSPACE,
    locate,
)
from .config import FILE_HEADER

# Folder of a package library's views (write_views), and the library of
# verified results that runs add to it (extend_package).
VIEWS = "views"
EXTENSIONS = "Extensions"
# What lies in a package besides its modules: its build, its views, and the
# module folders of the runs whose workspace it is.
OUTSIDE = (".lake", VIEWS, "Definitions", "Theorems", "Solutions")


def resolve(path: str) -> Path:
    return locate(path)


def load(path: str) -> str:
    """The library's Lean text, without `import` lines. A package is imported
    by the file header (config.FILE_HEADER), so its text is a note on where
    an agent reads it."""
    if is_package(path):
        return package_note(path)
    return drop_imports(resolve(path).read_text()).strip()


def package_note(path: str) -> str:
    roots = ", ".join(root_modules(path))
    return (
        f"-- Library: the Lean package {resolve(path).name}, imported by every\n"
        f"-- file of this run (root modules {roots}). Its statements, with proofs\n"
        "-- elided, lie in `library/` of a working directory with Lean tools, and\n"
        "-- `library/toc.md` lists its declarations by module."
    )


def is_package(path: str) -> bool:
    return resolve(path).is_dir()


def build_dir(path: str) -> Path:
    """Where Lean finds the package's modules (`LEAN_PATH`)."""
    return resolve(path) / ".lake" / "build" / "lib" / "lean"


def root_modules(path: str) -> list[str]:
    """The package's root modules: its top-level Lean files."""
    return sorted(
        file.stem
        for file in resolve(path).glob("*.lean")
        if file.stem != "lakefile"
    )


def modules(path: str) -> list[tuple[str, Path]]:
    """Every module of the package's libraries, by name: not the files that
    runs check in it, which is also their workspace."""
    package = resolve(path)
    return sorted(
        (".".join(file.relative_to(package).with_suffix("").parts), file)
        for file in package.rglob("*.lean")
        if file.relative_to(package).parts[0] not in OUTSIDE
        and file.stem != "lakefile"
    )


def write_views(path: str) -> Path:
    """The views of the package, in `views/`: each module's statements with
    proofs elided, and `toc.md`. Kept outside the modules, so that no build or
    `lean-check` reads them."""
    folder = resolve(path) / VIEWS
    shutil.rmtree(folder, ignore_errors=True)
    lines = ["| Module | Kind | Name | Line |", "|---|---|---|---|"]
    for name, file in modules(path):
        text = file.read_text()
        view = folder / file.relative_to(resolve(path))
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(statements_only(text))
        lines += [
            f"| {name} | {kind} | {declared} | {line} |"
            for kind, declared, line in declarations(text)
            if declared
        ]
    (folder / "toc.md").write_text("\n".join(lines) + "\n")
    return folder


def release(path: str) -> str:
    """The Lean release a package is built for, from its toolchain."""
    toolchain = (resolve(path) / "lean-toolchain").read_text().strip()
    return toolchain.rsplit(":", 1)[-1]


def release_workspace(lean_release: str) -> Path:
    if lean_release == DEFAULT_LEAN_RELEASE:
        return HOME_DIR / "orchestration_workspace"
    return RELEASES_DIR / lean_release / "workspace"


# Constructs a library may not use: they run code when it builds, or prove by
# trusting the compiler or an unchecked assumption.
UNSOUND = re.compile(r"\b(?:native_decide|implemented_by|extern)\b|^axiom\s", re.M)
MAX_PACKAGE_BYTES = 2**28


def package_files(archive: bytes) -> dict[PurePosixPath, bytes]:
    """The Lean files of a zip, below its one top folder if it has one."""
    with zipfile.ZipFile(io.BytesIO(archive)) as unpacked:
        infos = [
            info
            for info in unpacked.infolist()
            if not info.is_dir() and info.filename.endswith(".lean")
        ]
        if sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES:
            raise ValueError("the archive unpacks to too much data")
        paths = {info: PurePosixPath(info.filename) for info in infos}
        if any(p.is_absolute() or ".." in p.parts for p in paths.values()):
            raise ValueError("the archive has paths outside its folder")
        tops = {p.parts[0] for p in paths.values()}
        nested = len(tops) == 1 and all(len(p.parts) > 1 for p in paths.values())
        files = {
            PurePosixPath(*paths[info].parts[nested:]): unpacked.read(info)
            for info in infos
        }
    files = {
        path: text
        for path, text in files.items()
        if path.name != "lakefile.lean" and path.parts[0] not in OUTSIDE
    }
    if not any(len(path.parts) == 1 for path in files):
        raise ValueError("the archive has no root module (a top-level .lean file)")
    return files


def create_package(name: str, lean_release: str, archive: bytes) -> dict:
    """A package library `name` from a zip of Lean files, built on the
    packages of `lean_release`. Refused, and nothing kept, if a file runs code
    or proves unsoundly, the build fails, or a proof uses `sorry`."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"invalid library name {name}")
    target = LIBRARIES_DIR / name
    if target.exists() or (LIBRARIES_DIR / f"{name}.lean").exists():
        raise ValueError(f"library {name} exists")
    files = package_files(archive)
    texts = {path: data.decode("utf-8", "replace") for path, data in files.items()}
    for path, text in texts.items():
        found = sorted({*executable_constructs(text), *UNSOUND.findall(text)})
        if found:
            raise ValueError(f"{path} uses {', '.join(found)}")
    workspace = release_workspace(lean_release)
    roots = sorted(path.stem for path in files if len(path.parts) == 1)
    partial = LIBRARIES_DIR / f".{name}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    try:
        for path, text in texts.items():
            (partial / path).parent.mkdir(parents=True, exist_ok=True)
            (partial / path).write_text(text)
        for folder in ("Definitions", "Theorems", "Solutions"):
            (partial / folder).mkdir(exist_ok=True)
        (partial / f"{EXTENSIONS}.lean").write_text(
            "-- Verified results added by runs, one module each.\n"
        )
        lakefile = re.sub(
            r"package «\w+»", f"package «{name}»",
            (workspace / "lakefile.lean").read_text(),
        )  # fmt: skip
        lakefile += "".join(
            f"lean_lib «{root}» where\n" for root in [*roots, EXTENSIONS]
        )
        (partial / "lakefile.lean").write_text(lakefile)
        for file in ("lake-manifest.json", "lean-toolchain"):
            shutil.copy(workspace / file, partial / file)
        (partial / ".lake").mkdir()
        (partial / ".lake" / "packages").symlink_to(
            (workspace / ".lake" / "packages").resolve()
        )
        for root in [*roots, EXTENSIONS]:
            result = build_locked(partial, {}, root, timeout=3600)
            if not result.ok or result.has_sorry_warning:
                problem = (
                    result.error_report()[:2000]
                    if not result.ok
                    else f"{root} uses sorry"
                )
                raise ValueError(problem)
        partial.rename(target)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    write_views(str(target))
    return listing(target)


def listing(path: Path) -> dict:
    """A library as the page lists it."""
    if path.is_dir():
        toc = (path / VIEWS / "toc.md").read_text().splitlines()[2:]
        return {
            "name": path.name,
            "package": True,
            "release": release(str(path)),
            "modules": len({line.split("|")[1] for line in toc}),
            "declarations": len(toc),
        }
    text = path.read_text()
    return {
        "name": path.name,
        "chars": len(text),
        "declarations": len(declared_names(text)),
    }


def declared_names(library: str) -> set[str]:
    return set(declaration_names(library))


def contribution(solution: str, library_chars: int, theorem_name: str) -> str:
    """What a verified solution adds to the library it was built on: the text
    after the library prefix, with `solution` named after the target theorem."""
    body = (
        solution[len(FILE_HEADER) :]
        if solution.startswith(FILE_HEADER)
        else solution
    )
    added = body[library_chars:].strip()
    return re.sub(
        r"^theorem solution\b", f"theorem {theorem_name}", added, flags=re.M
    )


def add(path: str, result_dir: Path) -> dict:
    """Append the verified result of `result_dir` to the library at `path`."""
    result_dir = Path(result_dir)
    summary = modernize(json.loads((result_dir / "summary.json").read_text()))
    if not (summary.get("checks") or {}).get("verified"):
        return {
            "added": False,
            "declarations": [],
            "problem": "run is not verified",
        }
    solution = next(result_dir.glob("*_standalone.lean")).read_text()
    return extend(
        path,
        solution,
        (summary.get("library") or {}).get("chars", 0),
        summary["formalization"]["theorem_name"],
        f"{summary['run_id']} ({summary['uuid']}), {summary.get('status')}",
    )


def extend(
    path: str, solution: str, library_chars: int, theorem_name: str, source: str
) -> dict:
    """Append what `solution` adds to the library at `path`.

    Returns {"added": bool, "declarations": [...], "problem": str}. The
    library file is unchanged unless the extended file compiles cleanly.
    """
    added = contribution(solution, library_chars, theorem_name)
    if is_package(path):
        return extend_package(path, added, theorem_name, source)
    library_path = resolve(path)
    checker = LeanChecker(WORKSPACE, 1, 600)
    with open(library_path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = (
            library_path.read_text() if library_path.exists() else FILE_HEADER
        )
        if theorem_name in declaration_names(current, ("theorem",)):
            return {
                "added": False,
                "declarations": [],
                "problem": f"{theorem_name} is already in the library",
            }
        extended = f"{current.rstrip()}\n\n-- From run {source}.\n\n{added}\n"
        check_path = TEMP_DIR / "library_checks" / f"{theorem_name}.lean"
        check_path.parent.mkdir(parents=True, exist_ok=True)
        result = checker.check_text(extended, check_path)
        if not result.ok or result.has_sorry_warning:
            problem = (
                result.error_report()[:2000]
                if not result.ok
                else "extension contains sorry"
            )
            return {"added": False, "declarations": [], "problem": problem}
        library_path.write_text(extended)
        check_path.unlink(missing_ok=True)
    return {
        "added": True,
        "problem": "",
        "declarations": declaration_names(added),
    }


def extend_package(path: str, added: str, theorem_name: str, source: str) -> dict:
    """Add `added` to the package as a module of `Extensions`, which imports
    the earlier ones, and rebuild. The module and the root are restored if
    the build fails."""
    package = resolve(path)
    root = package / f"{EXTENSIONS}.lean"
    name = re.sub(r"\W", "_", theorem_name)
    module = package / EXTENSIONS / f"{name}.lean"
    if module.exists():
        return {
            "added": False,
            "declarations": [],
            "problem": f"{theorem_name} is already in the library",
        }
    constructs = executable_constructs(added)
    if constructs:
        return {
            "added": False,
            "declarations": [],
            "problem": rejected(constructs).output,
        }
    with build_lock(package):
        earlier = root.read_text() if root.exists() else ""
        imports = "".join(
            f"import {r}\n" for r in root_modules(path) if r != EXTENSIONS
        )
        text = (
            f"import Mathlib\n{imports}{earlier}set_option autoImplicit false"
            f"\n\n-- From run {source}.\n\n{added}\n"
        )
        root_text = f"{earlier}import {EXTENSIONS}.{name}\n"
        result = build_locked(
            package, {module: text, root: root_text}, EXTENSIONS
        )
        if not result.ok or result.has_sorry_warning:
            module.unlink()
            root.write_text(earlier)
            problem = (
                result.error_report()[:2000]
                if not result.ok
                else "extension contains sorry"
            )
            return {"added": False, "declarations": [], "problem": problem}
    write_views(path)
    return {
        "added": True,
        "problem": "",
        "declarations": declaration_names(added),
    }


USAGE = (
    "usage: python3 -m aiprover_orchestration.orchestrator.libraries add "
    "<library.lean> <result dir>, or views <package>"
)


def main() -> None:
    if sys.argv[1:2] == ["views"] and len(sys.argv) == 3:
        print(write_views(sys.argv[2]))
        return
    if len(sys.argv) != 4 or sys.argv[1] != "add":
        raise SystemExit(USAGE)
    record = add(sys.argv[2], Path(sys.argv[3]))
    print(json.dumps(record, indent=1))


if __name__ == "__main__":
    main()
