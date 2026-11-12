"""Pure orchestrator tests. No model, network, mini, or browser required."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_loop as m

TASKS = [
    {"id": name, "title": name, "acceptance": ["A"]}
    for name in m.STAGES
]


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name)
        self.patches = [
            patch.object(m, "ROOT", self.root),
            patch.object(m, "DATA", self.root / ".agent"),
            patch.object(m, "APP", self.root / "app"),
            patch.object(m, "STATE", self.root / ".agent" / "state.json"),
            patch.object(m, "BACKLOG", self.root / ".agent" / "backlog.json"),
            patch.object(m, "REVIEW", self.root / ".agent" / "review.json"),
            patch.object(m, "BROWSER_RESULTS", self.root / ".agent" / "browser_results.json"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for p in reversed(self.patches):
            p.stop()
        self.d.cleanup()

    def fake(self, decisions):
        calls = []
        decisions = iter(decisions)

        def work(task, args, is_fix, findings):
            calls.append((task["id"], "fix" if is_fix else "work"))

        def review(task, args, state):
            calls.append((task["id"], "review"))
            return next(decisions)

        return calls, work, review

    def test_fix_happens_before_next_task(self):
        calls, worker, reviewer = self.fake([
            (False, ["button broken"]), (True, []), (True, []),
        ])
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=worker), patch.object(m, "review", side_effect=reviewer):
            self.assertEqual(m.main(["--tasks", "2", "--fix-attempts", "2"]), 0)
        self.assertEqual(calls, [
            ("SCAFFOLD", "work"), ("SCAFFOLD", "review"),
            ("SCAFFOLD", "fix"), ("SCAFFOLD", "review"),
            ("CRUD", "work"), ("CRUD", "review"),
        ])
        self.assertEqual(m.read_json(m.STATE)["accepted"], ["SCAFFOLD", "CRUD"])

    def test_retry_limit_blocks_next_task(self):
        calls, worker, reviewer = self.fake([(False, ["broken"]), (False, ["still broken"])])
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=worker), patch.object(m, "review", side_effect=reviewer):
            self.assertEqual(m.main(["--fix-attempts", "1"]), 2)
        self.assertEqual({task for task, _ in calls}, {"SCAFFOLD"})
        state = m.read_json(m.STATE)
        self.assertEqual(state["active"]["phase"], "blocked")
        self.assertEqual(state["active"]["fixes_used"], 1)

    def test_resume_blocked_with_larger_budget(self):
        calls, worker, reviewer = self.fake([(False, ["broken"]), (False, ["still broken"])])
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=worker), patch.object(m, "review", side_effect=reviewer):
            self.assertEqual(m.main(["--fix-attempts", "1"]), 2)
        calls2, worker2, reviewer2 = self.fake([(True, [])])
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=worker2), patch.object(m, "review", side_effect=reviewer2):
            self.assertEqual(m.main(["--tasks", "1", "--fix-attempts", "2"]), 0)
        self.assertEqual(calls2, [("SCAFFOLD", "fix"), ("SCAFFOLD", "review")])

    def test_no_issue_without_opt_in(self):
        calls, worker, reviewer = self.fake([(False, ["bug"])])
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=worker), patch.object(m, "review", side_effect=reviewer), \
             patch.object(m, "create_github_issue") as gh:
            self.assertEqual(m.main(["--fix-attempts", "0"]), 2)
            gh.assert_called_once()
            self.assertFalse(gh.call_args.args[-1].github_issues)

    def test_crash_keeps_current_task(self):
        with patch.object(m, "check_dependencies"), patch.object(m, "plan", return_value=TASKS), \
             patch.object(m, "work", side_effect=RuntimeError("provider unavailable")):
            self.assertEqual(m.main(["--tasks", "3"]), 1)
        self.assertEqual(m.read_json(m.STATE)["active"]["id"], "SCAFFOLD")
        self.assertEqual(m.read_json(m.STATE)["accepted"], [])

    def test_backlog_validation(self):
        self.assertEqual(m.validate_backlog({"tasks": TASKS}), TASKS)
        with self.assertRaises(ValueError):
            m.validate_backlog({"tasks": TASKS[:2]})


if __name__ == "__main__":
    unittest.main()
