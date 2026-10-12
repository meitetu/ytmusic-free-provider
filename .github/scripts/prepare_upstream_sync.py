"""Merge upstream into a new fork branch, or commit a draft conflict report.

No network, credentials, pushes or conflict-resolution heuristics live here.
The caller fetches refs and publishes the result as a PR. JSON on stdout is
the only machine-readable output; Git diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def prepare(base: str, upstream: str, *, report_workflow_changes: bool = False) -> dict[str, str]:
    if git("status", "--porcelain").stdout:
        raise RuntimeError("Refusing to sync a checkout with local changes")
    base_sha = git(
        "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"
    ).stdout.strip()
    upstream_sha = git(
        "rev-parse", "--verify", "--end-of-options", f"{upstream}^{{commit}}"
    ).stdout.strip()
    ancestor = git("merge-base", "--is-ancestor", upstream_sha, base_sha, check=False)
    if ancestor.returncode == 0:
        return {"status": "unchanged", "upstream": upstream_sha, "base": base_sha}
    if ancestor.returncode != 1:
        raise RuntimeError(ancestor.stderr)
    branch = f"sync/upstream-{upstream_sha}"
    # Never reset/rewrite an existing sync branch. One branch per upstream SHA.
    git("switch", "--create", branch, base_sha)
    merge = git("merge", "--no-ff", "--no-commit", upstream_sha, check=False)
    print(merge.stdout + merge.stderr, file=sys.stderr)
    paths = []
    reason = ""
    if merge.returncode:
        paths = git("diff", "--name-only", "--diff-filter=U", "-z").stdout.split(
            "\0"
        )
        paths = [p for p in paths if p]
        git("merge", "--abort")
        if merge.returncode != 1 or not paths:
            raise RuntimeError("Merge failed without resolvable conflict metadata")
        status = "conflict"
        reason = "Content conflicts require manual resolution."
    elif report_workflow_changes:
        paths = git("diff", "--cached", "--name-only", "-z", "--", ".github/workflows").stdout.split("\0")
        paths = [p for p in paths if p]
        if paths:
            git("merge", "--abort")
            status = "workflow-permission"
            reason = (
                "Workflow changes require a GitHub App token with Workflows: write, "
                "or a maintainer's manual integration. The standard token must not "
                "push workflow-file changes."
            )
        else:
            status = "merged"
    else:
        status = "merged"
    if status != "merged":
        report = Path(".github/upstream-conflicts") / f"{upstream_sha}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            f"# Upstream merge requires manual integration\n\n"
            f"Fork base: `{base_sha}`\n\nUpstream: `{upstream_sha}`\n\n"
            f"Reason: {status}. {reason}\n\n"
            "This branch contains a report only. No upstream code was integrated.\n"
            "Keep this PR in draft. Merge the upstream SHA locally, review these paths,\n"
            "resolve any conflicts and test while preserving the fork patches,\n"
            "then remove this report.\n"
            "Do not use blanket ours/theirs strategies.\n\nPaths requiring review:\n\n"
            + "\n".join(
                f"- {json.dumps(path, ensure_ascii=False)}" for path in paths
            )
            + "\n",
            encoding="utf-8",
        )
        git("add", "--", str(report))
        git("commit", "-m", f"Report upstream {status} at {upstream_sha[:12]}")
    else:
        git("commit", "--no-edit")
    return {
        "status": status,
        "branch": branch,
        "upstream": upstream_sha,
        "base": base_sha,
        "head": git("rev-parse", "HEAD").stdout.strip(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="Fetched fork base ref")
    parser.add_argument("upstream", help="Fetched upstream ref")
    parser.add_argument("--report-workflow-changes", action="store_true",
                        help="Create a report-only draft instead of pushing workflow changes")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.base, args.upstream,
                                 report_workflow_changes=args.report_workflow_changes)))
    except (subprocess.CalledProcessError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
