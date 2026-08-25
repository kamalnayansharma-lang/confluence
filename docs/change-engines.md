# Change engines

The pipeline's code-writing step is pluggable. `pipeline/orchestrator.py`
only ever calls `deps.change_engine.implement_change(repo_dir, diff,
max_turns)` — it has no idea which underlying tool actually wrote the code.
Which one runs is a single config value: `CHANGE_AGENT_ENGINE` in `.env`
(also settable from the config UI at `/ui/config`).

| `CHANGE_AGENT_ENGINE` | Binary required on PATH | Install | Credential |
|---|---|---|---|
| `claude_code` (default) | `claude` | `npm install -g @anthropic-ai/claude-code` | `ANTHROPIC_API_KEY` |
| `cursor` | `agent` | `curl https://cursor.com/install -fsS \| bash` | `CURSOR_API_KEY` |
| `copilot` | `copilot` | `npm install -g @github/copilot` (Node 22+) | reuses `GITHUB_TOKEN` |
| `codex` | `codex` | `npm install -g @openai/codex` (Node 22+) | `OPENAI_API_KEY` |
| `gemini` | `gemini` | `npm install -g @google/gemini-cli` (Node 18+) | `GEMINI_API_KEY` |
| `antigravity` | `agy` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | none — OAuth only, see below |
| `kiro` | `kiro-cli` | `curl -fsSL https://cli.kiro.dev/install \| bash` | `KIRO_API_KEY` |

Only the engine you've selected needs its CLI installed and credential set —
`scripts/check_credentials.py` checks the CLI for whichever engine is
currently configured, and `bootstrap.sh` will install it for you if `npm`
(or, for `cursor`/`antigravity`, network access for their curl installers)
is available.

## Why a CLI subprocess instead of a "real" SDK for each one

All of these are terminal-native coding agents, not conventional REST APIs —
the vendor-supported way to drive them programmatically is to spawn the CLI
non-interactively and parse its output:

- `claude_code`: the `claude-agent-sdk` Python package does this under the
  hood (`claude_agent_sdk.query()` spawns `claude` and speaks a structured
  protocol over stdin/stdout) — see `agent/engines/claude_code.py`.
- Everything else: no Python SDK exists yet, so each engine module shells
  out directly via the shared subprocess helper in
  `agent/engines/_subprocess_utils.py`:
  - `cursor`: `agent -p --force --output-format json "<prompt>"`
  - `copilot`: `copilot -p "<prompt>" --allow-all-tools --no-ask-user -s`
  - `codex`: `codex exec --sandbox workspace-write --json -o <tmpfile> "<prompt>"`
    (the final response is read back from `<tmpfile>` rather than parsed out
    of the JSON-lines event stream on stdout)
  - `gemini`: `gemini -p "<prompt>" --yolo --output-format json`
  - `antigravity`: `agy -p "<prompt>" --dangerously-skip-permissions --output-format json`
  - `kiro`: `kiro-cli chat --no-interactive --trust-all-tools "<prompt>"`

None of these CLIs expose a native "max agentic turns" concept the way the
Claude Agent SDK does (Gemini CLI has one, `maxSessionTurns`, but it's a
`settings.json` value, not a flag, so mutating the user's Gemini config just
to set it wasn't worth the fragility). For every engine except `claude_code`,
`CHANGE_AGENT_MAX_TURNS` is instead converted into a wall-clock timeout
(`turns_to_timeout_seconds`, 60s/turn) — good enough to bound a runaway run,
not a faithful equivalent.

## `antigravity` is OAuth-only

Every other engine authenticates with a plain API key you can drop into
`.env` or the config UI. Antigravity CLI does not: "headless mode uses
cached credentials — authenticate once with an interactive `agy` session
first." A run with no cached credentials just fails with an
authentication-required error, which `AntigravityCliEngine` surfaces as a
failed `ChangeAgentResult` — there's nothing this codebase can do
programmatically to satisfy that. To actually use it: run `agy login`
interactively once (on the host, or inside the container with a TTY
attached), then headless runs reuse those cached credentials. In Podman,
that means mounting whatever directory `agy login` writes credentials to as
a volume, rather than relying on `.env`.

## `kiro` flags are documented, not live-verified

Every other engine's exact invocation in this file was confirmed against a
real run of that CLI. `kiro-cli`'s flags (`chat --no-interactive
--trust-all-tools`) and env var (`KIRO_API_KEY`) are sourced from Kiro's own
published headless-mode docs (kiro.dev/docs/cli/headless,
kiro.dev/changelog/cli/2-0) instead, since no local install was available to
test against. No JSON output format or usage/cost field is documented for
headless mode either, so `KiroCliEngine` treats stdout as the plain response
text and always reports `usage=None` — same "engine didn't report anything
parseable" convention used when Cursor's own JSON stats field is absent. If
this engine fails outright on first real use, check `kiro-cli chat --help`
against what `agent/engines/kiro_cli.py` actually sends before assuming the
change itself is broken.

## Adding another engine

1. Implement `ChangeEngine` (`agent/base.py`) — one async method,
   `implement_change(repo_dir, diff, max_turns) -> ChangeAgentResult`.
2. Put it in `agent/engines/`.
3. Register the name in `agent/factory.py::build_change_engine`.
4. Add it to `scripts/check_credentials.py::_ENGINE_REQUIREMENTS`, the
   `case` statements in `scripts/bootstrap.sh` and `Containerfile`, and the
   engine dropdown in `ui/config_fields.py`.

Nothing in `pipeline/orchestrator.py` or its tests needs to change — the
orchestrator tests already inject a mocked `change_engine` rather than
depending on any specific implementation.
