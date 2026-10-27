"""The Lean sandbox (lean/sandbox.py, Landlock on Linux and Seatbelt on
macOS): Lean elaborates in the project, and writes outside it and other
executables are refused."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from aiprover_orchestration.lean.checker import sandboxed
from aiprover_orchestration.lean.sandbox import seatbelt_arguments
from aiprover_orchestration.paths import WORKSPACE
from tests.support import TemporaryDirectoryTest, requires_lean


def run(command: list[str], project: Path) -> subprocess.CompletedProcess:
    """The command under the sandbox; a refusal is an error exit, whereas a
    signal (a negative code) means the sandbox itself failed."""
    return subprocess.run(
        sandboxed(command, project),
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )


@requires_lean
class SandboxTest(TemporaryDirectoryTest):

    def setUp(self) -> None:
        super().setUp()
        toolchain = (WORKSPACE / "lean-toolchain").read_text()
        (self.directory / "lean-toolchain").write_text(toolchain)
        (self.directory / "A.lean").write_text("theorem t : 1 + 1 = 2 := rfl\n")

    def test_lean_elaborates_in_the_project(self):
        result = run(["lean", "-o", "A.olean", "A.lean"], self.directory)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.directory / "A.olean").exists())

    def test_writes_outside_the_project_are_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "A.olean"
            result = run(["lean", "-o", str(target), "A.lean"], self.directory)
            self.assertGreater(result.returncode, 0, result.stderr)
            self.assertFalse(target.exists())

    def test_other_executables_are_refused(self):
        result = run(["/bin/sh", "-c", "true"], self.directory)
        self.assertGreater(result.returncode, 0, result.stderr)


class SeatbeltProfileTest(TemporaryDirectoryTest):

    def test_trees_are_parameters_of_a_deny_by_default_profile(self):
        tree = str(self.directory)
        arguments = seatbelt_arguments([], [tree], [])
        profile = arguments[2]
        self.assertIn("(deny default)", profile)
        self.assertNotIn(tree, profile)
        self.assertEqual(arguments[3:], ["-D", f"P0={tree}", "-D", f"P1={tree}"])
        self.assertIn('(allow file-write* (subpath (param "P1")))', profile)


if __name__ == "__main__":
    unittest.main()
