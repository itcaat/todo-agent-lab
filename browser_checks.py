"""Independent browser acceptance tests using real Microsoft Playwright CLI.

No application code is bundled with this project. These checks make the
model-created application obey an explicit, testable DOM contract.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class BrowserSetupError(RuntimeError):
    """Browser tooling not installed or couldn't launch."""


@dataclass
class BrowserReport:
    stage: str
    passed: bool = True
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    transcript: str = ""

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "checks": self.checks,
            "failures": self.failures,
            "transcript": self.transcript[-16000:],
        }


def require_playwright() -> str:
    exe = shutil.which("playwright-cli")
    if not exe:
        raise BrowserSetupError(
            "playwright-cli not found. Install: npm install -g @playwright/cli@latest"
        )
    return str(Path(exe).resolve())


class Browser:
    def __init__(self, exe: str, session: str = "todo-agent-lab-check"):
        self.exe = exe
        self.session = session
        self.transcript: list[str] = []

    def cmd(self, *args: str) -> str:
        cmd = [self.exe, f"-s={self.session}", *args]
        try:
            out = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=75,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise BrowserSetupError(f"Playwright CLI timed out: {args[0]}") from e
        self.transcript.append("$ playwright-cli " + " ".join(args) + "\n" + out.stdout[-1500:])
        if out.returncode:
            msg = out.stdout[-2500:]
            if re.search(r"browserType\.launch|Executable doesn't exist|install.*browser|browser.*not installed|chromium.*not found", msg, re.I):
                raise BrowserSetupError(
                    "Playwright browser isn't installed. Try: npx playwright install chromium\n" + msg
                )
            raise AssertionError(f"playwright-cli {' '.join(args[:2])}: {msg}")
        return out.stdout

    def truth(self, description: str, expression: str) -> None:
        # The CLI prints an explicit '### Result' block; don't mistake source
        # echoed elsewhere in output for an assertion result.
        script = f"() => (Boolean({expression}) ? 'TODO_LAB_TRUE' : 'TODO_LAB_FALSE')"
        result = self.cmd("eval", script)
        match = re.search(r"### Result\s*\n\s*[\"']?(TODO_LAB_TRUE|TODO_LAB_FALSE)", result)
        if not match or match.group(1) != "TODO_LAB_TRUE":
            raise AssertionError(f"{description}: expected true; actual: {result[-900:]}")

    def clean_storage(self) -> None:
        self.cmd("eval", "() => { localStorage.clear(); return 'cleared'; }")
        self.cmd("reload")

    def add(self, name: str) -> None:
        self.cmd("fill", '[data-testid="todo-input"]', name)
        self.cmd("click", '[data-testid="todo-add"]')

    def close(self) -> None:
        try:
            self.cmd("close")
        except Exception:
            pass


def check_stage(stage: str, url: str, exe: str | None = None) -> BrowserReport:
    exe = exe or require_playwright()
    report = BrowserReport(stage)
    browser = Browser(exe, session=f"todo-lab-auto-{os.getpid()}")

    def check(label: str, fn) -> bool:
        try:
            fn()
            report.checks.append(label)
            return True
        except AssertionError as e:
            report.failures.append(f"{label}: {str(e)[:1800]}")
            report.passed = False
            return False

    try:
        if not check("Open app", lambda: browser.cmd("open", url)):
            return report
        if not check("HTML title and input", lambda: browser.truth(
            "App scaffold", "document.querySelector('h1')?.textContent.includes('Todo') && "
            "!!document.querySelector('[data-testid=todo-input]') && "
            "!!document.querySelector('[data-testid=todo-add]')")):
            return report
        if stage == "SCAFFOLD":
            return report

        if not check("Reset browser storage", browser.clean_storage):
            return report
        if not check("Add a todo", lambda: browser.add("Buy milk")):
            return report
        if not check("Todo appears", lambda: browser.truth(
            "One task", "document.querySelectorAll('[data-testid=todo-item]').length === 1 && "
            "document.querySelector('[data-testid=todo-item]').textContent.includes('Buy milk')")):
            return report
        if not check("Reload preserves todo", lambda: (
            browser.cmd("reload"),
            browser.truth("Persist todo", "document.querySelectorAll('[data-testid=todo-item]').length === 1")
        )):
            return report
        if not check("Complete todo", lambda: (
            browser.cmd("check", '[data-testid="todo-toggle"]'),
            browser.truth("Checkbox checked", "document.querySelector('[data-testid=todo-toggle]').checked === true")
        )):
            return report
        if not check("Completion persists", lambda: (
            browser.cmd("reload"),
            browser.truth("Persist completed", "document.querySelector('[data-testid=todo-toggle]').checked === true")
        )):
            return report
        if not check("Delete todo", lambda: (
            browser.cmd("click", '[data-testid="todo-delete"]'),
            browser.truth("Deleted", "document.querySelectorAll('[data-testid=todo-item]').length === 0")
        )):
            return report
        if not check("Deletion persists", lambda: (
            browser.cmd("reload"),
            browser.truth("Persist deleted", "document.querySelectorAll('[data-testid=todo-item]').length === 0")
        )):
            return report
        if stage == "CRUD":
            return report

        if not check("Filter controls exist", lambda: browser.truth(
            "All filter controls", "['filter-all','filter-active','filter-completed'].every(k => !!document.querySelector('[data-testid='+k+']'))")):
            return report
        if not check("Reset to all filter", lambda: browser.cmd("click", '[data-testid="filter-all"]')):
            return report
        if not check("Add completed task", lambda: (
            browser.add("Done task"),
            browser.cmd("check", '[data-testid="todo-toggle"]'),
        )):
            return report
        if not check("Add active task", lambda: browser.add("Active task")):
            return report
        if not check("Active filter", lambda: (
            browser.cmd("click", '[data-testid="filter-active"]'),
            browser.truth("Active-only", "document.querySelectorAll('[data-testid=todo-item]').length === 1 && "
                          "document.querySelector('[data-testid=todo-item]').textContent.includes('Active task')")
        )):
            return report
        if not check("Completed filter", lambda: (
            browser.cmd("click", '[data-testid="filter-completed"]'),
            browser.truth("Completed-only", "document.querySelectorAll('[data-testid=todo-item]').length === 1 && "
                          "document.querySelector('[data-testid=todo-item]').textContent.includes('Done task')")
        )):
            return report
        check("All filter", lambda: (
            browser.cmd("click", '[data-testid="filter-all"]'),
            browser.truth("All tasks", "document.querySelectorAll('[data-testid=todo-item]').length === 2")
        ))
        return report
    finally:
        browser.close()
        report.transcript = "\n\n".join(browser.transcript)
