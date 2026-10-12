"""Run the workflow's actual standard-token CI gate with a local gh stub."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/sync-upstream.yml"
SHA = "a" * 40


class SyncCITests(unittest.TestCase):
    def run_gate(self, prs, runs):
        step = WORKFLOW.read_text().split("name: Ensure CI runs for a standard-token PR", 1)[1]
        block = step.split("        run: |\n", 1)[1]
        lines = []
        for line in block.splitlines():
            if line.strip() and not line.startswith("          "):
                break
            lines.append(line[10:] if line.strip() else "")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prs.json").write_text(json.dumps(prs))
            (root / "runs.json").write_text(json.dumps(runs))
            gh = root / "gh"
            gh.write_text(
                f"#!{sys.executable}\n"
                "import os,sys\n"
                "from pathlib import Path\n"
                "root=Path(os.environ['SYNC_CI_FIXTURES'])\n"
                "if sys.argv[1:3] == ['pr','list']: print((root/'prs.json').read_text())\n"
                "elif sys.argv[1:3] == ['run','list']: print((root/'runs.json').read_text())\n"
                "elif sys.argv[1:3] == ['workflow','run']:\n"
                " (root/'dispatch').write_text(' '.join(sys.argv[1:]))\n"
                "else: raise SystemExit('unexpected gh command')\n"
            )
            gh.chmod(0o755)
            # The job's scratch files remain isolated from other tests/processes.
            script = "\n".join(lines).replace("/tmp/sync-pr.json", str(root / "pr-out.json"))
            script = script.replace("/tmp/sync-ci.json", str(root / "ci-out.json"))
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "SYNC_CI_FIXTURES": directory,
                "GITHUB_REPOSITORY": "meitetu/ytmusic-free-provider",
                "SYNC_BRANCH": "sync/upstream-" + SHA,
            }
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            dispatch = root / "dispatch"
            return dispatch.read_text() if dispatch.exists() else None

    def pr(self, **overrides):
        return {"headRefOid": SHA, "isDraft": False, "isCrossRepository": False, **overrides}

    def test_new_pr_dispatches_ci_on_fork_branch(self):
        command = self.run_gate([self.pr()], [])
        self.assertEqual(
            command,
            f"workflow run test.yml --repo meitetu/ytmusic-free-provider --ref sync/upstream-{SHA}",
        )

    def test_draft_cross_repository_missing_or_ambiguous_pr_never_dispatches(self):
        for prs in ([], [self.pr(isDraft=True)], [self.pr(isCrossRepository=True)],
                    [self.pr(), self.pr()]):
            with self.subTest(prs=prs):
                self.assertIsNone(self.run_gate(prs, []))

    def test_exact_head_success_or_running_ci_is_not_duplicated(self):
        for event in ("push", "pull_request", "workflow_dispatch"):
            for status, conclusion in (("queued", ""), ("in_progress", ""),
                                       ("completed", "success")):
                with self.subTest(event=event, status=status):
                    run = {"headSha": SHA, "event": event, "status": status,
                           "conclusion": conclusion}
                    self.assertIsNone(self.run_gate([self.pr()], [run]))

    def test_failed_cancelled_stale_or_unrelated_runs_cannot_skip_ci(self):
        success = {"headSha": SHA, "event": "workflow_dispatch", "status": "completed",
                   "conclusion": "success"}
        for overrides in ({"conclusion": "failure"}, {"conclusion": "cancelled"},
                          {"headSha": "b" * 40}, {"event": "schedule"}):
            with self.subTest(overrides=overrides):
                self.assertIsNotNone(self.run_gate([self.pr()], [{**success, **overrides}]))


if __name__ == "__main__":
    unittest.main()
