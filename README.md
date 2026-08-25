# confluence-pr-agent

Watches a Confluence spec page. When it changes, diffs the change against
the last-seen version, implements the corresponding code change (plus tests)
in a target GitHub repo using a pluggable code-writing engine (Claude Code,
Cursor, GitHub Copilot, OpenAI Codex, Gemini CLI, Antigravity CLI, or Kiro CLI — see
[docs/change-engines.md](docs/change-engines.md)), opens a PR describing the
change and linking back to the Confluence page, and emails the team a
summary. A small web UI (`/ui/*`) covers configuring credentials, browsing
run history, and triggering a run without Postman/curl.

Named for exactly what it does end to end — Confluence change in, PR out —
rather than a cuter name that would obscure the pipeline's shape.

## Architecture

```
Confluence page edited
        |
        v
  [Confluence webhook: page_updated]
        |
        v
+-------------------+
| Webhook receiver   |  FastAPI, verifies HMAC signature, extracts page id,
| (webhook/app.py)   |  hands off to the pipeline as a background task
+-------------------+
        |
        v
+-------------------+
| Confluence client  |  Fetches current page body/version via Confluence
| (confluence/)      |  Cloud REST API; diff.py unified-diffs it against the
|                     |  last-seen version in the page store.
+-------------------+
        |
        v
+-------------------+
| Page store          | JSON file (data/page_store.json) tracking the last
| (storage/)           | processed version+body per page id. Only advanced
|                       | on a fully successful pipeline run.
+-------------------+
        |
        v  (diff text)
+-------------------+
| Git client          | Clones TARGET_REPO, creates a branch.
| (repo/git_client.py) |
+-------------------+
        |
        v
+-------------------+
| Change engine         | Pluggable (CHANGE_AGENT_ENGINE): Claude Code SDK,
| (agent/)              | Cursor CLI, or GitHub Copilot CLI. Runs agentically
|                       | inside the cloned repo, implements the change AND
|                       | its tests, returns a PR-ready summary. No hardcoded
|                       | "which file" logic --- the model decides from the
|                       | diff + repo contents. See docs/change-engines.md.
+-------------------+
        |
        v
+-------------------+
| Test runner           | Runs TARGET_REPO_TEST_COMMAND inside the repo.
| (testing/)              | Failure stops the pipeline before any push/PR.
+-------------------+
        |
        v (retried up to CHANGE_AGENT_MAX_ATTEMPTS times on failure)
+-------------------+
| LLM Judge               | Advisory, not a gate (JUDGE_ENABLED): an
| (judge/)                 | independent model (JUDGE_PROVIDER: anthropic |
|                           | openai) scores implements_spec/scoped/
|                           | tests_cover_behavior independently and never
|                           | blocks the PR -- it only changes what kind of
|                           | PR opens next. Fails open if unconfigured or
|                           | the call errors.
+-------------------+
        |
        v
+-------------------+
| Git + GitHub client    | Commits, pushes, and opens a PR (or, if a PR from
| (repo/)                 | a prior run for this page is still open, pushes
|                         | onto its branch and updates it in place instead
|                         | of opening a duplicate). A judge rejection makes
|                         | this a draft titled "[Needs Work]" with an
|                         | agent:needs-work label and the rubric in the PR
|                         | body; a warning gets an agent:warning label.
+-------------------+
        |
        v
+-------------------+
| Email client            | Sends a summary + PR link to the team via
| (notifications/)          | SendGrid's HTTP API.
+-------------------+
```

All of this is tied together in `pipeline/orchestrator.py::run_pipeline`,
which the webhook receiver calls as a FastAPI background task.

## Web UI

Four unauthenticated pages, all under `/ui/`:

- **`/ui/how-it-works`** — a walkthrough of the pipeline stage by stage, for
  narrating in a demo instead of a separate slide deck. The overview diagram
  at the top is built from the same `pipeline/stages.py` sequence and
  `ui/pipeline_flow.py` component that `/ui/runs/{id}` uses, so it can't
  drift out of sync with the real pipeline.
- **`/ui/config`** — edit every credential and setting listed in
  `.env.example` from a form instead of a text file. Secret fields are
  write-only: the current value is never redisplayed, and submitting one
  blank leaves it unchanged rather than clearing it. Picking a
  `CHANGE_AGENT_ENGINE` shows only that engine's credential field. Saves
  write straight to `.env` and take effect immediately (`get_settings()`
  cache is cleared on save) — no restart needed.
- **`/ui/runs`** — every pipeline invocation, newest first: page, engine
  used, status, duration, and the PR link. Includes no-op runs (duplicate
  webhook redeliveries, version bumps with unchanged content) so you can see
  the dedup guards working, not just successful runs.
- **`/ui/simulate`** — trigger a run without Postman or hand-written curl.
  Enter a page ID (+ optional title/space key), and it builds the exact
  webhook payload Confluence sends, signs it if `CONFLUENCE_WEBHOOK_SECRET`
  is set, and dispatches it through the real `/webhook/confluence` route
  in-process (via `httpx.ASGITransport`, not a real HTTP round-trip — see
  `ui/routes.py` for why). Shows the payload and the response.

Unauthenticated is fine for a POC bound to `127.0.0.1` (the default in
`podman-compose.yml`); add auth before exposing this beyond a trusted
network, since `/ui/config` both reads and writes secrets.

## Project layout

```
src/confluence_pr_agent/
  config.py            Settings loaded from env vars / .env
  models.py             Shared dataclasses passed between stages
  webhook/               FastAPI receiver + webhook payload parsing
  confluence/             Confluence REST client + diffing (+ checksum dedup)
  storage/                 JSON page-version store + run-history store
  repo/                     git CLI wrapper + GitHub REST client (PRs)
  agent/                     Pluggable change engine (base.py, factory.py, engines/)
  testing/                    Runs the target repo's test suite as a gate
  judge/                        Pluggable LLM judge (base.py, factory.py, providers/)
  notifications/                SendGrid client + email templates
  pipeline/                      Orchestrator tying every stage together
  ui/                              Config / runs / webhook-simulator pages
tests/                             Tests for THIS service's pipeline logic
  fixtures/                        Sample webhook payload + Confluence API responses
scripts/
  bootstrap.sh                    Automated setup (see SETUP.md)
  check_credentials.py             Read-only credential sanity checks
```

## Running it

See [SETUP.md](SETUP.md) for the full manual-vs-automated credential setup.
This service is meant to run containerized — the container starts the
service itself via its `CMD` (no separate `uvicorn` command to remember or
keep alive):

```bash
cp .env.example .env   # fill in credentials via the UI once it's running, or by hand now

podman build -f Containerfile -t confluence-pr-agent \
  --build-arg CHANGE_AGENT_ENGINE=claude_code .   # or cursor/copilot/codex/gemini/antigravity/kiro

podman run -d --name confluence-pr-agent \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  --env-file .env \
  -v "$(pwd)/data:/app/data:Z" \
  -v "$(pwd)/.env:/app/.env:Z" \
  confluence-pr-agent
```

The `.env` bind mount (not just `--env-file`) matters: `--env-file` only
sets process environment variables at container start, it doesn't put an
actual file inside the container. Without the mount, `/ui/config` has
nothing to write to and edits wouldn't survive a restart. `podman-compose.yml`
does the same thing if you have `podman-compose` installed
(`podman-compose up -d --build`).

**Switching engines** means rebuilding with a different `--build-arg`
(the image only installs the CLI for the engine you picked — see
[docs/change-engines.md](docs/change-engines.md)); update `CHANGE_AGENT_ENGINE`
in `.env` to match before restarting, or the container will have a CLI
installed that doesn't match what the running service is configured to use.

Then open http://localhost:8000/ui/config to fill in remaining credentials,
and http://localhost:8000/ui/simulate to trigger a run — no `.env`
hand-editing or curl required.

**Bare local run** (no container — fastest loop while iterating on this
service's own code):
```bash
./scripts/bootstrap.sh
source .venv/bin/activate
uvicorn confluence_pr_agent.webhook.app:app --reload --port 8000
```

## Tests

These test the pipeline itself (webhook parsing, diffing + checksum dedup,
page/run stores, GitHub/SendGrid clients, change-engine factory, the UI
routes, orchestration logic with the change engine and git operations mocked
out) — not the code the agent generates in a target repo.

```bash
source .venv/bin/activate
python -m pytest
```

## Design notes / POC limitations

- **Page/run stores are single JSON files.** Fine for one process; swap for
  SQLite or a real DB before running multiple instances concurrently
  (`storage/page_store.py` / `storage/run_store.py` are the only places that
  would need to change).
- **Redelivered/no-op webhooks are cheap, on purpose.** Two independent
  guards in `confluence/diff.py::compute_diff` stop a redundant webhook from
  triggering a real pipeline run: an exact version match short-circuits
  immediately (before fetching/diffing anything), and a sha256 checksum of
  the normalized plain-text body catches the case where Confluence bumps the
  version on a metadata-only edit (labels, restrictions, a no-op "restore
  this version") that doesn't change the visible content. Both show up as
  `no_change_detected` runs in `/ui/runs`, not silently-dropped requests.
- **Retries are diff-based, not queue-based.** A failed run (agent failure,
  failing tests, PR API error) does not advance the stored page version, so
  the next webhook delivery for that page retries from the same diff. There's
  no separate retry queue or backoff in this POC.
- **Only labeled pages are processed, if any labels are configured.**
  `CONFLUENCE_ALLOWED_LABELS` (managed via the tag-list editor at
  `/ui/config`) is checked right after fetching the page, before any diff
  computation, cloning, or agent work — so an unlabeled page (meeting notes,
  design docs, an unreviewed BRD) costs nothing beyond the initial fetch.
  Empty = no filtering, everything is eligible. Intended workflow: a BRD
  author writes the spec with no knowledge of which repo(s) are involved; a
  tech reviewer applies the label once it's actually ready for the agent to
  act on — the label doubles as an approval gate, not just a category.
  Shows up as an `ignored` run in `/ui/runs`, not a silently-dropped request.
- **HTML-to-text diffing is a lightweight regex strip**, not a full XHTML
  parser (`confluence/diff.py::_to_plain_text`) — good enough to produce a
  readable diff for the agent's prompt, not a faithful markup
  reconstruction.
- **The change engine has broad tool access** (file read/write/edit, shell)
  inside the *cloned working copy only* — it never touches your actual
  machine outside `data/workdirs/`. Review PRs before merging like you would
  any other automated change.
- **`cursor` and `copilot` engines have no native turn limit**, unlike the
  Claude Agent SDK — `CHANGE_AGENT_MAX_TURNS` becomes a wall-clock timeout
  for those two instead (see `docs/change-engines.md`).
