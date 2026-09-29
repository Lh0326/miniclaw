# MiniClaw

[![CI](https://github.com/Lh0326/miniclaw/actions/workflows/ci.yml/badge.svg)](https://github.com/Lh0326/miniclaw/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[简体中文](README.md) · English · [Architecture (Chinese)](docs/architecture.md)

A lightweight terminal AI Agent Harness written in Python, with an explicit agent loop,
capability-based approvals, controlled file delivery, and recoverable conversation history.

MiniClaw connects directly to an OpenAI-compatible streaming HTTP endpoint through `httpx`.
It uses no Agent framework or provider SDK; `httpx` is its single **direct runtime dependency**.
The repository makes the runtime mechanics visible, testable, and easy to inspect.

## What it implements

| Area | Implementation |
| --- | --- |
| Agent execution | Explicit state transitions, streamed tool-call assembly, turn/tool budgets |
| Tool governance | Declared capabilities mapped to `ALLOW`, `ASK`, or `DENY` |
| Built-in file delivery | Edit a workspace copy, list changed files, review content, approve application |
| Recovery | SHA-256 checkpoint checks and unmatched tool-call detection |
| Streaming reliability | Bounded retries before the first event; no automatic stream restart afterward |
| Audit performance | Cached event IDs and per-run sequences for incremental append validation |
| Delegation | Child tool allowlists intersected with parent permissions; depth limits |
| Background work | Scheduled tasks deny approvals that cannot be obtained unattended |
| Extensibility | Skills, memory, planning, and a handwritten stdio MCP client |
| Offline validation | Fake/scripted models, injected faults, and deterministic evaluations |

## Quick start

Use **Python 3.12+**, `uv`, and a POSIX environment such as Linux or macOS.
The current configuration persistence and interactive REPL require POSIX facilities;
native Windows is not supported for those paths. On Windows, use a Linux environment such as WSL.

```bash
git clone https://github.com/Lh0326/miniclaw.git
cd miniclaw
uv python install 3.12
uv sync --locked --extra dev --python 3.12
uv run python examples/full_agent_flow.py --fake
```

The complete demo needs **no API key**. It uses a scripted model and temporary directories to:

- Load a Skill and create a plan.
- Delegate a task, write a file, and reject a high-risk command.
- Save memory, create and cancel a scheduled job, and write a trace.
- Confirm the source is unchanged before explicitly applying the generated artifact.

Successful output includes these stable lines (dynamic run IDs omitted):

```text
high-risk command denied: yes
memory saved: yes
scheduled job status: cancelled
source unchanged before apply: yes
applied artifacts: notes.txt
trace written: yes
final response: offline MiniClaw flow completed
```

Other focused examples are in [examples/](examples/):
`minimal_harness.py`, `streaming_repl.py`, `safe_file_agent.py`,
`resumable_project_agent.py`, and `governed_multi_agent.py`.

## Connect a model

Configure a streaming Chat Completions endpoint and a model identifier supplied by your provider:

```bash
uv run miniclaw config set base_url https://YOUR_PROVIDER/v1
uv run miniclaw config set model YOUR_MODEL_ID
uv run miniclaw config set api_key
```

The last command prompts for the key, avoiding a key argument in shell history.
`OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` environment variables take precedence.
See [.env.example](.env.example) for names; MiniClaw does not automatically load `.env` files.
Complete the configuration commands first: a fresh installation still prompts for initial
configuration even when the environment variables are present. `config show` displays
the configuration file and defaults, not the final values after environment overrides.

Start the installed CLI from the project you want the Agent to work on:

```bash
cd /path/to/your/project
/path/to/miniclaw/.venv/bin/miniclaw
```

Provider compatibility depends on its streaming and tool-call protocol.
The offline results below measure harness behavior, not real-model task quality.

## Architecture

```text
Terminal / local commands
  └─ MiniClawApp
      └─ AgentLoop: explicit state machine + execution budgets
          ├─ Context: budget trimming, Skills, memory, planning
          ├─ Model: httpx → SSE decoder → model events
          ├─ Tools: assembly → validation → permission → execution
          │   ├─ Built-in files → workspace copy
          │   ├─ Shell / Git → selected Process or Docker backend → workspace copy
          │   ├─ Web → HTTP on the host
          │   ├─ MCP → external host process → server-configured files/services
          │   └─ Subagents / scheduled work
          ├─ Workspace copy → list changed files → content review → approved apply
          └─ SQLite / checkpoints / event log / traces
```

Read the [architecture guide](docs/architecture.md) for module links and implementation boundaries.
The [Chinese README](README.md) also documents configuration, Skills, and MCP setup.

### Recovery and streaming guarantees

Checkpoint loading verifies a stored SHA-256 digest. Conversation history is inspected for
tool calls with no matching result. Unfinished non-idempotent or unknown tools require
human approval before the session can resume.
After recovery is allowed, missing tool results are filled with explicit error results
stating that execution was interrupted and the outcome is unknown. Existing results are
preserved so the next model request has paired calls/results. Resume restores message
history; it does not execute those tools again, restore an unapplied workspace copy,
or guarantee exactly-once side effects.

Before the first emitted event, the HTTP client can retry rate limits (`429`), server errors
(`5xx`), and timeouts using bounded exponential backoff with jitter.
A numeric `Retry-After` is honored up to the configured delay cap.
Once any event has been emitted, errors propagate without restarting the stream.

## Tools and terminal commands

| Category | Built-in tools |
| --- | --- |
| Files | `read_file`, `write_file` |
| Shell | `run_command` |
| Git | `git_status`, `git_log`, `git_diff`, `git_show` |
| Web | `fetch_url`, optional configured `web_search` |
| Memory | `remember`, `search_memory` |
| Planning | `create_plan`, `list_tasks`, `start_task`, `complete_task`, `fail_task` |
| Delegation | `delegate_task` |
| Scheduling | `schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task` |
| MCP | Tools exposed by configured stdio servers |

`/changes` prints the sandbox path and changed file names; it does not show a line-by-line diff.
Use an editor or compare a file in a separate terminal before approving it:

```bash
git diff --no-index -- /path/to/project/utils.py /path/to/sandbox/workspace/utils.py
```

Replace both paths with your project and the sandbox path shown by `/changes`.
Exit code `1` means differences were found. Open newly created files directly for review.
Then use `/apply <path>` or `/apply --all` to approve writing artifacts back. `/sessions` lists sessions;
`/resume <id>` restores a saved conversation. `/help` lists the remaining commands.

## Docker and MCP execution scope

With Docker running and model configuration complete, prepare an image and explicitly
select the command backend from the MiniClaw checkout:

```bash
docker pull python:3.12-slim
uv run miniclaw --workspace /path/to/your/project --require-strong-sandbox --docker-image python:3.12-slim
```

Run `/sandbox` in the REPL and check that `selected` is `strong`. Docker installation alone
does not enable this backend. The image must already exist because runtime uses `--pull never`;
an unavailable backend fails startup rather than falling back to Process.

Docker covers Shell/Git commands routed through the executor. Built-in file tools operate
on the copy through the host Python process; model calls, Web requests, and MCP processes
also remain on the host. The default image has no project dependencies or Git: prepare
a suitable image and select it with `--docker-image`. Commands run without network access.

The filesystem MCP example in the [Chinese README](README.md#mcp) passes `"."` relative
to the **real project directory**. Approved writes can modify original files immediately,
without `/apply` or the Docker command backend. MCP servers start during application
initialization, before individual tool-call approval. Try the example in a dedicated demo
project. MCP tool calls are treated as non-idempotent operations with side effects and
require approval; see the [MCP architecture notes](docs/architecture.md#mcp-执行与审批).

## Verification

The repository has **500+ automated tests** and **10 core offline Evals**.
See [CI](https://github.com/Lh0326/miniclaw/actions/workflows/ci.yml) for the current branch.
The reproducible publication baseline, commit
[e4152fd](https://github.com/Lh0326/miniclaw/commit/e4152fd3ef4cc2dac0d6752ce8b6791fee6a7cee),
produced these local results on **Linux with Python 3.12**;
its [CI run](https://github.com/Lh0326/miniclaw/actions/runs/36577936215) also checked Linux/macOS and packaging:

| Check | Result |
| --- | --- |
| Automated tests | **516 passed, 1 skipped**; the skip required an unavailable Docker image |
| Core offline Evals | **10 / 10 passed** |
| Ruff | Passed |
| Wheel build and clean installation | Passed, including package import and CLI help |

```bash
uv run ruff check .
uv run pytest -q
uv run python -m miniclaw.evals.cli run tests/fixtures/evals/core.json --output /tmp/miniclaw-evals
uv run python examples/full_agent_flow.py --fake
uv build
```

The 10 core Evals check text assembly, completion status, expected event-type presence,
and Unicode output. Their event assertion checks presence, not strict ordering.
Separate automated tests cover retry limits, authentication failures, timeouts,
injected disconnections, checkpoint recovery, and event-log behavior.
[CI](.github/workflows/ci.yml) runs offline checks on Linux/macOS and a clean wheel install on Linux.
Linux CI prepares the Docker image and runs the real-container tests. Those integration
tests skip when Docker or its image is unavailable, including on the macOS runner.

## Current boundaries

- The default process backend provides no strong process, filesystem, or network isolation.
  A workspace copy controls ordinary artifact delivery; use an appropriately configured Docker backend when isolation is required.
- Apply approval binds the path list, not content hashes, and multi-file application is not a transaction.
- Token estimation is heuristic; context trimming drops lower-priority content without summarizing it.
- Child Agents share the model provider and sandbox; their permissions are constrained separately.
- The event index is an append optimization, not a cross-process locking guarantee.
- Traces redact configured secrets and sensitive fields; other persisted stores are not universally redacted.
- MCP supports stdio tools only; web fetching does not execute JavaScript.
- Scheduler coordination uses a local SQLite database and is not designed for multi-host deployment.
- Native Windows configuration persistence and the POSIX interactive REPL are unsupported.

## Contributing and license

Read the [contribution guide](CONTRIBUTING.md) and [security notes](SECURITY.md).
Issues and focused pull requests are welcome. Include a reproducible example for bugs,
and run the relevant offline checks before submitting behavior changes.
Do not commit credentials, local configuration, traces, or session data.

MiniClaw is released under the [MIT License](LICENSE).
