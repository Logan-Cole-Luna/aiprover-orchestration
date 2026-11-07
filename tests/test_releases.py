"""Lean releases: the packages that match a release, and their installation."""

import os
import unittest
from pathlib import Path
from unittest import mock

from aiprover_orchestration.lean import releases
from tests.support import TemporaryDirectoryTest

TAGS = {
    "mathlib": {"v4.34.1", "v4.34.0", "v4.35.0", "v4.31.0"},
    "REPL": {"v4.34.0", "v4.35.0"},
    "cslib": {"v4.34.1", "v4.35.0"},
}


class MatchingTest(unittest.TestCase):
    def test_mathlib_is_exact_and_small_packages_may_skip_a_patch(self):
        self.assertEqual(
            releases.matching_tags("v4.34.1", TAGS),
            {"mathlib": "v4.34.1", "REPL": "v4.34.0", "cslib": "v4.34.1"},
        )
        self.assertIsNone(releases.matching_tags("v4.36.0", TAGS))
        self.assertEqual(set(releases.offers(TAGS)), {"v4.35.0", "v4.34.1"})

    def test_a_requested_release_cannot_leave_its_folder(self):
        self.assertFalse(releases.is_installed("../results/run"))


class InstallTest(TemporaryDirectoryTest):
    def test_an_installation_ends_on_a_linked_workspace(self):
        def run(command, cwd, env, check):
            if command[:2] == ["lake", "update"]:
                (Path(cwd) / "lake-manifest.json").write_text("{}")
            (Path(cwd) / ".lake" / "packages" / "mathlib" / ".lake").mkdir(
                parents=True, exist_ok=True
            )

        with (
            mock.patch.object(releases, "RELEASES_DIR", self.directory),
            mock.patch.object(releases, "tags", side_effect=TAGS.values()),
        ):
            releases.install("v4.35.0", run)
            self.assertTrue(releases.is_installed("v4.35.0"))
            workspace = releases.directory("v4.35.0") / "workspace"
            text = (workspace / "lakefile.lean").read_text()
            project = releases.project_path("v4.35.0")
            build = project / ".lake" / "packages" / "mathlib" / ".lake"
            # Installed package builds are read-only to the runs that link them.
            self.assertFalse(os.access(build, os.W_OK))
            releases.set_writable(project, True)
        self.assertEqual(text.count('@ "v4.35.0"'), 3)
        self.assertTrue((workspace / ".lake" / "packages").is_symlink())
