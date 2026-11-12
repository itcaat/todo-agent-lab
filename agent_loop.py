#!/usr/bin/env python3
"""Planner -> Worker -> independent Browser QA + Reviewer -> Fixer loop.

mini-SWE-agent is the default agent engine. An optional Codex CLI adapter is
available for users who authenticate via their ChatGPT account.

The application is NOT implemented in this repository: agents create app/*.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from browser_checks import BrowserSetupError, check_stage, require_playwright

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
DATA = ROOT / ".agent"
STATE = DATA / "state.json"
BACKLOG = DATA / "backlog.json"
REVIEW = DATA / "review.json"
BROWSER_RESULTS = DATA / "browser_results.json"
STAGES = ("SCAFFOLD", "CRUD", "FILTERS")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_digest():
    """Detect accidental Reviewer writes to application sources."""
    h = hashlib.sha256()
    for p in sorted(APP.rglob("*")):
        if p.is_file() and not p.name.startswith(".DS_Store"):
            h.update(str(p.relative_to(APP)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def log_event(state, kind, **details):
    state.setdefault("history", []).append({"time": now(), "event": kind, **details})
    write_json(STATE, state)


def check_dependencies(args):
    exe = "mini" if args.engine == "mini" else "codex"
    if not shutil.which(exe):
        raise RuntimeError(f"Missing '{exe}'. See README.md / Installation")
    if args.engine == "mini" and not (args.model or os.getenv("MSWEA_MODEL_NAME")):
        raise RuntimeError("mini requires --model MODEL or MSWEA_MODEL_NAME (and provider credentials)")
    require_playwright()


def make_browser_shim(real_exe: str):
    """Log the Reviewer's actual Playwright calls (not just its self-report)."""
    shimdir = DATA / "bin"
    shimdir.mkdir(parents=True, exist_ok=True)
    shim = shimdir / "playwright-cli"
    shim.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$AGENT_BROWSER_LOG"\n'
        'exec "$PLAYWRIGHT_CLI_BINARY" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shimdir


def run_agent(role: str, instruction: str, args, env_add: dict | None = None):
    """Invoke one fresh mini-SWE-agent or Codex CLI session from project root."""
    if args.engine == "mini":
        model = args.model or os.environ["MSWEA_MODEL_NAME"]
        cmd = ["mini", "-y", "-m", model, "-t", instruction]
    else:
        cmd = ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check"]
        if role == "reviewer" and args.allow_codex_network:
            cmd += ["-c", "sandbox_workspace_write.network_access=true"]
        if args.model:
            cmd += ["--model", args.model]
        cmd += [instruction]

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_add:
        env.update(env_add)
    logpath = DATA / f"{role}.log"
    print(f"  [{role}] {args.engine} running (log: {logpath.relative_to(ROOT)})", flush=True)
    try:
        with logpath.open("w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True,
                                  stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=args.agent_timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{role}: time limit exceeded ({args.agent_timeout}s). Check {logpath}") from e
    if proc.returncode:
        tail = "\n".join(logpath.read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
        raise RuntimeError(f"{role}: agent exit {proc.returncode}\n{tail}\nFull log: {logpath}")


def validate_backlog(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list):
        raise ValueError(".agent/backlog.json must contain a 'tasks' array")
    tasks = obj["tasks"]
    if [t.get("id") for t in tasks] != list(STAGES):
        raise ValueError(f"Planner must create exactly these ordered IDs: {STAGES}")
    for task in tasks:
        if not isinstance(task.get("title"), str) or not task["title"].strip():
            raise ValueError("Each task needs a title")
        if not isinstance(task.get("acceptance"), list) or not task["acceptance"]:
            raise ValueError("Each task needs at least one acceptance criterion")
    return tasks


def plan(args):
    if BACKLOG.exists():
        return validate_backlog(read_json(BACKLOG))
    prompt = f"""ROLE: Planner. Read GOAL.md and inspect the repository. There is NO existing Todo app.
Your ONLY deliverable is `{BACKLOG.relative_to(ROOT)}` as VALID JSON, in exactly this form:
{{"tasks":[
  {{"id":"SCAFFOLD","title":"...","acceptance":["...", "..."]}},
  {{"id":"CRUD","title":"...","acceptance":["...", "..."]}},
  {{"id":"FILTERS","title":"...","acceptance":["...", "..."]}}
]}}

Follow the three milestone definitions in GOAL.md precisely. Write detailed acceptance
criteria and make each milestone independently deliverable. The first milestone
MUST leave a functioning app/index.html. The second milestone implements full
CRUD and persistence; third adds filters. DO NOT write application code yet.
No issues or PRs, no git pushes. Save the JSON file; then finish.
"""
    run_agent("planner", prompt, args)
    return validate_backlog(read_json(BACKLOG))


def task_text(task):
    return f"{task['id']}: {task['title']}\nAcceptance criteria:\n" + "\n".join(
        f"- {criterion}" for criterion in task["acceptance"]
    )


def work(task, args, is_fix: bool, findings: list[str]):
    mode = "Fixer" if is_fix else "Worker"
    diagnosis = "\n".join("- " + x for x in findings) if findings else "None"
    prompt = f"""ROLE: {mode} — autonomous software engineering agent.
Read GOAL.md, inspect `app/`, and implement ONLY the current milestone.
The product starts from zero; you create the real app source files yourself.

CURRENT MILESTONE:
{task_text(task)}

LATEST REVIEW FINDINGS:
{diagnosis}

HARD RULES:
- The Reviewer MUST pass this milestone before the Planner can select another.
- {('Fix the concrete failures above FIRST; do not work on future milestones.' if is_fix else 'Implement this milestone only; do not pre-plan another task.')}
- Browser acceptance tests require the exact data-testid selectors from GOAL.md.
- Serve with Python http.server; make app/index.html work without a build step.
- Edit only `app/` (you may add files there). Do not edit GOAL.md or orchestrator.
- Prefer plain HTML, CSS, vanilla JS, localStorage; no npm dependencies.
- Test the relevant logic with local commands when possible.
- Do not access external accounts, do not create GitHub issues, don't commit or push.
Finish when the code changes are made.
"""
    run_agent("fixer" if is_fix else "worker", prompt, args)


@contextlib.contextmanager
def static_server():
    APP.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    output = DATA / "server.log"
    with output.open("w", encoding="utf-8") as logfile:
        proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(APP)],
            cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT,
        )
        try:
            url = f"http://127.0.0.1:{port}/"
            for _ in range(40):
                if proc.poll() is not None:
                    raise RuntimeError("Static HTTP server exited. See .agent/server.log")
                try:
                    urllib.request.urlopen(url, timeout=0.5).close()
                    break
                except urllib.error.HTTPError:
                    break  # 404 for missing index.html is an app bug, not a setup error
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Static HTTP server did not start")
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def review(task, args, state) -> tuple[bool, list[str]]:
    real_cli = require_playwright()
    with static_server() as url:
        print(f"  [browser] Testing {url} against {task['id']} milestone", flush=True)
        browser_report = check_stage(task["id"], url, exe=real_cli)
        write_json(BROWSER_RESULTS, browser_report.as_dict())
        if browser_report.passed:
            print("  [browser] deterministic acceptance: PASS", flush=True)
        else:
            print("  [browser] deterministic acceptance: FAIL", flush=True)
            for failure in browser_report.failures:
                print("    - " + failure[:240])

        REVIEW.unlink(missing_ok=True)
        shimdir = make_browser_shim(real_cli)
        browser_log = DATA / "reviewer_browser_calls.log"
        browser_log.write_text("", encoding="utf-8")
        env = {
            "PATH": str(shimdir) + os.pathsep + os.environ.get("PATH", ""),
            "PLAYWRIGHT_CLI_BINARY": real_cli,
            "AGENT_BROWSER_LOG": str(browser_log),
            "PLAYWRIGHT_CLI_SESSION": "todo-lab-review",
        }
        before = source_digest()
        prompt = f"""ROLE: independent Browser Reviewer. Your job is to TEST, never change product code.
Read GOAL.md and `.agent/browser_results.json` (deterministic findings).
Inspect `app/` if necessary. A REAL web app is running at {url}.

CURRENT MILESTONE:
{task_text(task)}

MANDATORY: use the actual `playwright-cli` shell command AT LEAST ONCE to open
this URL, snapshot the DOM and manually exercise the milestone's interactions.
Example: `playwright-cli open {url}`; `playwright-cli snapshot`;
`playwright-cli fill '[data-testid="todo-input"]' 'Milk'`;
`playwright-cli click '[data-testid="todo-add"]'`;
`playwright-cli reload`; `playwright-cli console`; `playwright-cli screenshot`.
The CLI is installed. Use a separate session via PLAYWRIGHT_CLI_SESSION.
Capture at least one screenshot as an artifact, but do not claim visual design
verification unless your model actually received and inspected its pixels.
DO NOT claim a pass without observing browser behavior, and do NOT modify `app/`.
Failures in `.agent/browser_results.json` are authoritative regardless of your opinion.

Create VALID JSON at `.agent/review.json` with exactly these main keys:
{{"task_pass":true,"issues":[],"summary":"What you actually saw in browser"}}
or
{{"task_pass":false,"issues":[{{"title":"...","description":"...","steps":["..."]}}],"summary":"..."}}

Issue descriptions should include actionable reproduction steps; do not create
GitHub Issues yourself. Finish only after writing `.agent/review.json`.
"""
        run_agent("reviewer", prompt, args, env_add=env)
        after = source_digest()
        if before != after:
            raise RuntimeError("Reviewer modified app sources; review must be read-only. Inspect app/ before retrying")
        if not browser_log.read_text(encoding="utf-8").strip():
            # Browser use is enforced by the independent tests and wrapper trace.
            # Do not accept a claim of browser testing without tool calls.
            return False, ["Reviewer did not call playwright-cli; no real browser evidence from Reviewer"]
        report = read_json(REVIEW)
        if not isinstance(report, dict) or not isinstance(report.get("task_pass"), bool):
            return False, ["Reviewer did not write valid .agent/review.json with task_pass boolean"]
        issues = []
        for x in report.get("issues", []):
            if isinstance(x, dict):
                issues.append(f"{x.get('title', 'Issue')}: {x.get('description', '')}; steps: {x.get('steps', [])}")
            else:
                issues.append(str(x))
        issues = browser_report.failures + issues
        ok = browser_report.passed and report["task_pass"] and not issues
        if not ok and not issues:
            issues = [str(report.get("summary", "Reviewer rejected task without details"))]
        return ok, issues


def create_github_issue(task, findings: list[str], state: dict, args):
    if not args.github_issues:
        print("  [github] Disabled; enable with --github-issues --repo OWNER/REPO")
        return
    if not args.repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        print("  [github] Invalid/missing --repo OWNER/REPO. Skipping issue creation.")
        return
    if not shutil.which("gh"):
        print("  [github] gh CLI not installed. Skipping issue creation.")
        return
    marker = f"[todo-lab:{task['id']}]"
    try:
        existing = subprocess.run(
            ["gh", "issue", "list", "--repo", args.repo, "--state", "all", "--limit", "100",
             "--search", f"{marker} in:title", "--json", "number,title"],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        if existing.returncode:
            raise RuntimeError(existing.stderr.strip())
        for issue in json.loads(existing.stdout):
            if marker in issue["title"]:
                print(f"  [github] Existing issue #{issue['number']}; no duplicate created")
                return
        body = (
            f"Automated Todo Browser Agent Lab finding.\n\n"
            f"Milestone: `{task['id']}` — {task['title']}\n\n"
            "## Reproduction / review findings\n\n" +
            "\n".join("- " + x for x in findings) +
            "\n\n## Expected behavior\n\n" +
            "\n".join("- " + x for x in task["acceptance"]) +
            "\n\nThe local harness exhausted its fix budget. Verify before production use.\n"
        )
        bodyfile = DATA / "github_issue_body.md"
        bodyfile.write_text(body, encoding="utf-8")
        created = subprocess.run(
            ["gh", "issue", "create", "--repo", args.repo,
             "--title", f"{marker} Browser QA fails: {task['title']}", "--body-file", str(bodyfile)],
            cwd=ROOT, text=True, capture_output=True, timeout=40,
        )
        if created.returncode:
            raise RuntimeError(created.stderr.strip())
        print("  [github] Created: " + created.stdout.strip())
        state.setdefault("issues", {})[task["id"]] = created.stdout.strip()
        write_json(STATE, state)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"  [github] Failed to create issue: {exc}. Local findings remain in .agent/")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Autonomous Todo app from zero: mini-SWE-agent + Playwright")
    p.add_argument("--engine", choices=["mini", "codex"], default="mini")
    p.add_argument("--model", help="Model name passed to the agent (mini provider model or codex model)")
    p.add_argument("--tasks", type=int, default=3, help="Max milestones to accept in this run")
    p.add_argument("--fix-attempts", type=int, default=2, help="Max Fixer runs per milestone")
    p.add_argument("--agent-timeout", type=int, default=900, help="Seconds per agent invocation")
    p.add_argument("--github-issues", action="store_true", help="Opt in: create issue when retries exhausted")
    p.add_argument("--allow-codex-network", action="store_true",
                   help="Allow network access to Codex Reviewer sandbox (needed on some systems for localhost)")
    p.add_argument("--repo", help="GitHub repository OWNER/REPO for issues")
    args = p.parse_args(argv)
    if args.tasks < 1 or args.fix_attempts < 0 or args.agent_timeout < 1:
        p.error("--tasks, --agent-timeout must be positive; --fix-attempts must be nonnegative")
    DATA.mkdir(exist_ok=True)
    state = read_json(STATE, {"accepted": [], "active": None, "history": [], "issues": {}})
    try:
        check_dependencies(args)
        backlog = plan(args)
        accepted_this_run = 0
        while accepted_this_run < args.tasks:
            active = state.get("active")
            if active is None:
                task = next((x for x in backlog if x["id"] not in state["accepted"]), None)
                if task is None:
                    print("\nGOAL COMPLETE: all three browser milestones accepted")
                    return 0
                state["active"] = {"id": task["id"], "phase": "new", "fixes_used": 0, "findings": []}
                active = state["active"]
                log_event(state, "selected", task=task["id"])
            else:
                task = next((x for x in backlog if x["id"] == active["id"]), None)
                if task is None:
                    raise RuntimeError("Active task not found in backlog")
            print(f"\n==== {task['id']} : {task['title']} ====", flush=True)

            if active["phase"] == "blocked":
                if active["fixes_used"] < args.fix_attempts:
                    active["phase"] = "fixing"
                else:
                    print("FIX LIMIT REACHED. Increase --fix-attempts to continue THIS task.")
                    return 2
            if active["phase"] in ("new", "fixing"):
                fixing = active["phase"] == "fixing"
                if fixing:
                    active["fixes_used"] += 1
                    print(f"  [priority] FIX first ({active['fixes_used']}/{args.fix_attempts})")
                else:
                    print("  [worker] Building current milestone")
                # Persist intent before invoking agent; crash won't choose another task.
                log_event(state, "fix_start" if fixing else "work_start", task=task["id"])
                work(task, args, fixing, active.get("findings", []))
                active["phase"] = "review"
                log_event(state, "implementation_complete", task=task["id"])

            print("  [reviewer] Independent browser acceptance + AI review", flush=True)
            passed, findings = review(task, args, state)
            active["findings"] = findings
            log_event(state, "review_pass" if passed else "review_fail", task=task["id"], findings=findings)
            if passed:
                state["accepted"].append(task["id"])
                state["active"] = None
                accepted_this_run += 1
                log_event(state, "task_accepted", task=task["id"])
                print(f"  [PASS] {task['id']} accepted; next milestone is now allowed")
            else:
                print("  [FAIL] Current milestone rejected. Next task is BLOCKED.")
                for finding in findings:
                    print("    - " + finding[:280])
                if active["fixes_used"] >= args.fix_attempts:
                    active["phase"] = "blocked"
                    log_event(state, "fix_limit", task=task["id"])
                    create_github_issue(task, findings, state, args)
                    print("  [STOP] fix limit reached; resume same task with larger --fix-attempts")
                    return 2
                active["phase"] = "fixing"
                write_json(STATE, state)
        print(f"\nRun limit reached ({args.tasks} accepted milestones). Run again to continue.")
        return 0
    except (RuntimeError, ValueError, OSError, BrowserSetupError) as exc:
        print(f"\nSETUP / AGENT ERROR: {exc}", file=sys.stderr)
        print("State kept in .agent/state.json; retry does not move to next task.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
