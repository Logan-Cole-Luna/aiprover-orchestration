"""Lean releases a run can use. A release other than the repository's pin lies
in lean_releases/<release>/ of the home directory: `project`, the Lean project
of the AIProver harness with the packages, and `workspace`, the orchestration's
Lake project, which links those packages. Lean, Mathlib, REPL, and CSLib are
installed together, since a Mathlib release fits one Lean release only.

    python -m aiprover_orchestration.lean.releases v4.28.0
"""

import fcntl
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..paths import DEFAULT_LEAN_RELEASE, HOME_DIR, RELEASES_DIR, ROOT, STATE_DIR
from .sandbox import ELAN_HOME

PACKAGES = {
    "mathlib": "https://github.com/leanprover-community/mathlib4",
    "REPL": "https://github.com/leanprover-community/repl.git",
    "cslib": "https://github.com/leanprover/cslib",
}
STABLE = re.compile(r"v4\.(\d+)\.(\d+)")
REPL_BINARY = ".lake/packages/REPL/.lake/build/bin/repl"
# Disk use of an installed release, measured on v4.35.0, with a margin.
NEEDED_GB = 16
LAKEFILE = """import Lake
open Lake DSL

{requires}

package «project» where

@[default_target]
lean_lib «Project» where
"""
PROBE = "import Mathlib\ntheorem probe : (1 : Nat) + 1 = 2 := rfl\n"


def requirement(name: str, tag: str) -> str:
    return f'require {name} from git\n  "{PACKAGES[name]}" @ "{tag}"'


def tags(url: str) -> set[str]:
    """The stable release tags of a repository."""
    listing = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", url],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    names = (line.rsplit("/", 1)[-1] for line in listing.splitlines())
    return {name for name in names if STABLE.fullmatch(name)}


def key(release: str) -> tuple[int, int]:
    minor, patch = STABLE.fullmatch(release).groups()
    return int(minor), int(patch)


def package_tag(release: str, available: set[str]) -> str | None:
    """The tag of a package for `release`: the latest of its minor version up
    to `release`, since small packages skip patch releases and a later patch
    may require a later toolchain."""
    earlier = [
        tag
        for tag in available
        if key(tag)[0] == key(release)[0] and key(tag) <= key(release)
    ]
    return max(earlier, key=key, default=None)


def matching_tags(release: str, tag_sets: dict[str, set[str]]) -> dict | None:
    """The tag of each package for `release`, or None if one has none. Mathlib
    must match exactly, since it pins the Lean toolchain."""
    if release not in tag_sets["mathlib"]:
        return None
    chosen = {name: package_tag(release, found) for name, found in tag_sets.items()}
    return chosen if all(chosen.values()) else None


def offers(tag_sets: dict[str, set[str]]) -> dict[str, dict]:
    """The releases all packages support, with the tag of each package."""
    matches = {tag: matching_tags(tag, tag_sets) for tag in tag_sets["mathlib"]}
    return {tag: chosen for tag, chosen in matches.items() if chosen}


def directory(release: str) -> Path:
    return RELEASES_DIR / release


def project_path(release: str) -> Path:
    return directory(release) / "project"


def is_installed(release: str) -> bool:
    """Whether `release`, which a request may name, is installed."""
    return release == DEFAULT_LEAN_RELEASE or bool(
        STABLE.fullmatch(release) and (directory(release) / "ready").exists()
    )


def installing_release() -> str:
    """The release being installed, if any: the installer holds the lock of
    `start_install` until it exits, however it ends."""
    path = RELEASES_DIR / "install.lock"
    if not path.exists():
        return ""
    with open(path) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return lock.read().strip()
    return ""


def state(release: str) -> str:
    if is_installed(release):
        return "installed"
    if installing_release() == release:
        return "installing"
    # A log without `ready` or a running installer is a failed attempt.
    log = directory(release) / "install.log"
    return "failed" if log.exists() else "available"


def installed_releases() -> set[str]:
    found = {path.parent.name for path in RELEASES_DIR.glob("*/ready")}
    return {DEFAULT_LEAN_RELEASE, *found}


def free_gb() -> float:
    return round(shutil.disk_usage(HOME_DIR).free / 2**30, 1)


def link_workspace(release: str, chosen: dict[str, str]) -> None:
    """The orchestration's Lake project on the packages of the release."""
    workspace = directory(release) / "workspace"
    project = project_path(release)
    template = ROOT / "orchestration_workspace" / "lakefile.lean"
    text = template.read_text().replace(DEFAULT_LEAN_RELEASE, release)
    for name, tag in chosen.items():
        text = re.sub(
            rf'require {name} from git\s+"[^"]*" @ "[^"]*"',
            requirement(name, tag),
            text,
        )
    for folder in ("Definitions", "Theorems", "Solutions", ".lake"):
        (workspace / folder).mkdir(parents=True, exist_ok=True)
    (workspace / "lakefile.lean").write_text(text)
    shutil.copy(project / "lean-toolchain", workspace / "lean-toolchain")
    shutil.copy(project / "lake-manifest.json", workspace / "lake-manifest.json")
    packages = workspace / ".lake" / "packages"
    packages.unlink(missing_ok=True)
    packages.symlink_to((project / ".lake" / "packages").resolve())


def set_writable(project: Path, writable: bool) -> None:
    """Make the packages' build outputs writable, or read-only once
    installed. Sessions reach them through links; read-only, a Lake process
    that finds a stale trace fails in its own session instead of rebuilding
    Mathlib under every run of the release."""
    for build in (project / ".lake" / "packages").glob("*/.lake"):
        subprocess.run(
            ["chmod", "-R", "u+w" if writable else "a-w", str(build)],
            check=True,
        )


def install(release: str, run=subprocess.run) -> None:
    """Download Lean, Mathlib's cache, and the packages of `release`, build
    the packages, and check the result on a small file."""
    chosen = matching_tags(
        release, {name: tags(url) for name, url in PACKAGES.items()}
    )
    if chosen is None:
        raise SystemExit(f"{release}: not all packages support this release")
    project = project_path(release)
    project.mkdir(parents=True, exist_ok=True)
    set_writable(project, True)
    toolchain = f"leanprover/lean4:{release}"
    (project / "lean-toolchain").write_text(toolchain + "\n")
    (project / "lakefile.lean").write_text(
        LAKEFILE.format(
            requires="\n\n".join(
                requirement(name, tag) for name, tag in chosen.items()
            )
        )
    )
    (project / "Project.lean").write_text("import Mathlib\n")
    probe = project / "Probe.lean"
    probe.write_text(PROBE)

    # Mathlib's update hook would fetch its cache before the toolchain check.
    environment = {
        **os.environ,
        "PATH": f"{ELAN_HOME / 'bin'}:{os.environ.get('PATH', '')}",
        "MATHLIB_NO_CACHE_ON_UPDATE": "1",
    }

    def step(*command: str) -> None:
        print("$", " ".join(command), flush=True)
        run(list(command), cwd=project, env=environment, check=True)

    step("elan", "toolchain", "install", toolchain)
    step("lake", "update")
    # A package may have moved the project to its own toolchain.
    found = (project / "lean-toolchain").read_text().strip()
    if found != toolchain:
        raise SystemExit(f"lake update moved {release} to {found}")
    step("lake", "exe", "cache", "get")
    # The cache may lack artifacts that a later `lake setup-file` requires
    # (the `.ir` files of Lean 4.35); building now completes them here
    # rather than under a run.
    step("lake", "build", "Mathlib")
    # `REPL` alone is its library; the harness runs the executable.
    step("lake", "build", "cslib", "REPL/repl")
    step("test", "-x", REPL_BINARY)
    step("lake", "env", "lean", probe.name)
    link_workspace(release, chosen)
    set_writable(project, False)
    (directory(release) / "ready").touch()
    print("installed", flush=True)


def start_install(release: str) -> bool:
    """Install `release` in a process that outlives the server; False if
    another installation holds the lock. One runs at a time, so that each has
    the disk space it was checked for."""
    folder = directory(release)
    folder.mkdir(parents=True, exist_ok=True)
    with open(RELEASES_DIR / "install.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        lock.truncate(0)
        lock.write(release)
        lock.flush()
        with open(folder / "install.log", "w") as log:
            subprocess.Popen(
                [sys.executable, "-m", __name__, release],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                start_new_session=True,
                pass_fds=(lock.fileno(),),
            )
    return True


def harness_config(source: Path, release: str) -> Path:
    """The harness configuration `source` on the Lean project of `release`.
    It keeps its file name, which identifies the run's server, and lies in the
    state of this server: releases are shared between users' servers, whose
    configurations may have one name."""
    target = STATE_DIR / "harness" / release / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        re.sub(
            r"^lean_project\s*=.*$",
            f'lean_project = "{project_path(release)}"',
            source.read_text(),
            flags=re.M,
        )
    )
    return target


if __name__ == "__main__":
    release_name = sys.argv[1]
    try:
        install(release_name)
    except (Exception, SystemExit):
        shutil.rmtree(project_path(release_name), ignore_errors=True)
        raise
