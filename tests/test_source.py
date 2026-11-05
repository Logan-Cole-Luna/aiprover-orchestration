"""Reference material in a call's project."""

import tempfile
import unittest
from pathlib import Path

from aiprover_orchestration.agents import common


class SourceTest(unittest.TestCase):
    def test_source_is_a_copy_the_model_cannot_write_through(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes").mkdir()
            (root / "notes" / "a.tex").write_text("text")
            (root / "work").mkdir()
            common.add_source(root / "work", str(root / "notes"))
            copied = root / "work" / "source" / "a.tex"
            copied.write_text("changed")
            self.assertEqual((root / "notes" / "a.tex").read_text(), "text")


if __name__ == "__main__":
    unittest.main()
