# Todo Agent Lab — autonomous development + real browser review

**The `app/` directory is deliberately empty.** This experiment builds a Todo
List *from scratch* using an AI engineering agent, then validates it in a real
Chromium browser through Microsoft Playwright CLI.

The state machine is strict:

```text
Planner (write .agent/backlog.json, once)
  -> Worker (write app/)
  -> Deterministic Playwright CLI checks + AI Browser Reviewer
      PASS -> mark milestone accepted -> next milestone
      FAIL -> Fixer (same milestone) -> run browser checks and Reviewer again
                -> after retry limit, stop (optional GitHub Issue)
```

Three milestones: SCAFFOLD -> CRUD -> FILTERS. The next milestone is NEVER
selected while there is a failed or blocked current milestone. A new launch
resumes from `.agent/state.json` instead of starting over.

## Prerequisites (macOS / Linux)

- Python 3.11+ and Node.js 20+
- A working **tool-calling model/provider** for mini-SWE-agent (or Codex CLI)
- Chromium browser for Playwright

```bash
cd todo-agent-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install -g @playwright/cli@latest
# Install the browser if Chromium is not already available:
npx --yes playwright@latest install chromium
```

Verify: `mini --help`, `playwright-cli --help`. Optional: `playwright-cli show`
opens the live browser session viewer.

### Option A — mini-SWE-agent (main experiment)

**mini-SWE-agent does NOT automatically reuse `codex login` or ChatGPT
subscription access**. It uses a model provider configured through its own
LiteLLM-compatible backend. Set API credentials for your provider and select
an available model **with native tool-calling support**.

Example using an OpenAI API key (billed separately from ChatGPT):

```bash
export OPENAI_API_KEY="..."
python3 agent_loop.py --engine mini --model openai/gpt-4.1-mini --tasks 3 --fix-attempts 2
```

For OpenRouter, set `OPENROUTER_API_KEY` and use the appropriate model name
for your provider. For Ollama/OpenAI-compatible proxies, see
[mini-SWE-agent local models guide](https://mini-swe-agent.com/latest/models/local_models/).
If unsure, run `mini-extra config setup` first. Not every free model supports
native bash tool calls reliably.

### Option B — same loop using your existing Codex ChatGPT login

If you already authenticated via `codex login`:

```bash
codex login status
python3 agent_loop.py --engine codex --tasks 3 --fix-attempts 2
```

This uses the installed Codex CLI as the agent engine (not mini-SWE-agent).
The Planner, Worker, Reviewer and Fixer logic is otherwise identical.
If the model from your `~/.codex/config.toml` is not supported for ChatGPT login
(e.g. you saw a `model_not_found` error), provide a working Codex model with
`--model MODEL` or update the Codex configuration first.
If your Codex Reviewer can't reach `localhost` inside the workspace-write
sandbox, you can explicitly enable network access for that role:

```bash
python3 agent_loop.py --engine codex --allow-codex-network --tasks 3 --fix-attempts 2
```

**Security:** `--allow-codex-network` grants wider network access to the
Reviewer sandbox, not just localhost. Prefer a disposable workspace and
avoid credentials and sensitive files in the project. mini-SWE-agent in local
`-y` mode executes shell commands with your user's permissions; a working
directory is **not a security sandbox**. For stronger isolation, use Docker.

## Optional: automatic GitHub issues for unresolved failures

GitHub Issues are **off by default**. To enable them, connect `gh` and use an
existing repository where you have permission to create Issues:

```bash
gh auth login
gh auth status
python3 agent_loop.py --engine codex --tasks 3 --fix-attempts 2 \
  --github-issues --repo YOUR_USERNAME/YOUR_REPO
```

After all retries are exhausted, the orchestrator creates one issue describing
browser failures and steps to reproduce. It searches for an existing issue
with the same milestone marker before creation. It does **not** push code,
open PRs or create GitHub repositories. If the GitHub call fails, findings
remain locally under `.agent/`.

## How to watch the experiment

```bash
# Separate terminal: live browser session dashboard
playwright-cli show

# See latest reviewer summary and objective browser checks
cat .agent/review.json
cat .agent/browser_results.json
cat .agent/state.json

# Inspect model runs
cat .agent/worker.log
cat .agent/fixer.log
cat .agent/reviewer.log
```

The app source is created in `app/`; after the first accepted milestone you
can serve it independently:

```bash
python3 -m http.server 8000 --directory app
# open http://127.0.0.1:8000/
```

## Tuning / experiments

- `--tasks 1`: implement and review one milestone, then stop; subsequent run resumes.
- `--fix-attempts 0`: stop on first failed review (no repair attempts).
- `--fix-attempts 3`: give Fixer up to 3 repairs for the *same* milestone.
- `--agent-timeout 1200`: increase timeout per model invocation.
- For a blocked milestone, restart with a **larger** `--fix-attempts`. The
  old work and findings remain on disk.
- To start truly fresh, remove `.agent/` and generated `app/*` (keep
  `app/.gitkeep`). This removes local history and generated app code.

`GOAL.md` contains the product requirements and stable DOM selectors used by
independent browser checks. You can alter the product specification, but the
three milestone IDs and their browser tests are intentionally fixed in this
experiment; modifying the task model requires editing `browser_checks.py`.

## Tests for the orchestrator itself

These tests need no API keys, browsers or agent SDK:

```bash
python3 -m unittest discover -s tests -v
```

## Limitations

- This is a learning harness, not a production-safe multi-agent platform.
- Browser acceptance tests are deterministic and independent of the LLM,
  but only cover selected behaviors; the Reviewer explores other flows.
- A read-write local Reviewer is instructed not to edit `app/`. The
  orchestrator detects modifications but does not provide filesystem sandboxing.
- No real mini-SWE-agent / Codex or Chromium sessions were run while preparing
  this archive. The 9 included unit tests use simulated agent and browser output.
