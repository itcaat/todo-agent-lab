"""Exercise the Playwright CLI command sequence using a fake executable.
This tests browser harness wiring; it does NOT emulate a real browser.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_checks import check_stage


class BrowserTestSequence(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.root = Path(self.d.name)
        self.log = self.root / "commands.jsonl"
        self.exe = self.root / "fake-playwright-cli"
        # Fast POSIX shell fake: avoid spawning Python repeatedly for every CLI action.
        self.exe.write_text(
            "#!/bin/sh\n"
            + f"echo \"$*\" >> {str(self.log)!r}\n"
            + "case \"$*\" in\n"
            + "  *\" eval \"*) printf '### Result\\n\"TODO_LAB_TRUE\"\\n';;\n"
            + "  *) printf '### Page\\n- Page URL: http://127.0.0.1:9999/\\n';;\n"
            + "esac\n"
        )
        self.exe.chmod(0o755)

    def actions(self):
        return [line.split()[1] for line in self.log.read_text().splitlines()]

    def test_scaffold_has_real_open_and_dom_evaluation(self):
        report = check_stage("SCAFFOLD", "http://127.0.0.1:9999", str(self.exe))
        self.assertTrue(report.passed)
        actions = self.actions()
        self.assertEqual(actions[0], "open")
        self.assertIn("eval", actions)
        self.assertEqual(actions[-1], "close")
        self.assertNotIn("fill", actions)

    def test_crud_runs_browser_interactions_and_reload(self):
        report = check_stage("CRUD", "http://127.0.0.1:9999", str(self.exe))
        self.assertTrue(report.passed)
        actions = self.actions()
        for action in ("fill", "click", "check", "reload", "eval"):
            self.assertIn(action, actions)
        self.assertGreaterEqual(actions.count("reload"), 3)

    def test_filters_clicks_more_and_reviews_two_todos(self):
        report = check_stage("FILTERS", "http://127.0.0.1:9999", str(self.exe))
        self.assertTrue(report.passed)
        self.assertGreaterEqual(self.actions().count("click"), 5)


if __name__ == "__main__":
    unittest.main()
