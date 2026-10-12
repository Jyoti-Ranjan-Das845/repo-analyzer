# repo-analyzer

Scores how much work it takes to make a safe change in each part of a codebase.

You give it a project folder. An AI agent reads the code and writes a **component map**: which code belongs to
which component and subcomponent. The tool checks the map, completes it where needed, and scores every component
and subcomponent with eleven metrics. The result is a single HTML page.

```bash
repo-analyzer analyze /path/to/project
open /path/to/project/.repo-analyzer/report.html
```

- [Requirements](#requirements) · [Install](#install) · [Usage](#usage) · [What it writes](#what-it-writes)
- [How it works](#how-it-works) · [The score](#the-score) · [Reliability](#reliability) · [Safety](#safety)
- [Configuration](#configuration) · [Development](#development) · [Project layout](#project-layout)

## Requirements

- Python 3.11 or newer.
- Omnigent (the `omni` command; tested with 0.16.0) and the Claude CLI (`claude`), signed in to a Claude
  subscription. The agent runs through Omnigent's `claude-sdk` harness; no API key is needed.
- Optional: `git`. Inside a git checkout, git decides which files belong to the project.

## Install

```bash
git clone https://github.com/Jyoti-Ranjan-Das845/repo-analyzer.git
cd repo-analyzer
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs the `repo-analyzer` command into `.venv/bin/` (`python -m repo_analyzer` works the same).

## Usage

```bash
repo-analyzer analyze <project folder>    # the whole analysis; ends with report.html
repo-analyzer check   <project folder>    # check the component map
repo-analyzer score   <project folder>    # complete, score and report the current map, without the agent
repo-analyzer pieces  <project folder>    # list the functions, classes and methods a map can name
```

Options of `analyze`:

| Option | Effect |
|---|---|
| `--fresh` | Start over instead of continuing an earlier run |
| `--model NAME` | The agent's model (default `claude-opus-5-5[1m]`) |
| `--effort LEVEL` | The agent's reasoning effort: `low`, `medium`, `high`, `xhigh` or `max` (default `max`) |
| `--max-sessions N` | At most N agent sessions of about 30 minutes (default: scaled to the project's size) |
| `--skip-preflight` | Do not check Omnigent and Claude before starting |
| `--quiet` | Do not print every file the agent opens |

A run prints its progress as it goes. This is a real run on [itsdangerous](https://github.com/pallets/itsdangerous):

```
repo-analyzer: /work/itsdangerous
  22:15:52  preflight omni 0.16.0 · claude 2.1.296 · claude-opus-5-5[1m] answers
  22:15:52  read      50 text files (git), 131 named pieces
  22:15:52  agent     agent session 1 of up to 4
              read   .repo-analyzer/task/map-format.md
              read   .repo-analyzer/task/tree.md
              shell  git ls-files
              shell  grep -n '^## ' .repo-analyzer/task/pieces.md
              read   src/itsdangerous/__init__.py
              ...
  22:22:02  agent     the agent reports the map complete (1 session(s))
  22:22:02  check     the map is complete and consistent
  22:22:02  complete  no corrections needed
  22:22:02  score     3 components, 9 subcomponents; effort 2,912, 306 per 100 lines
  22:22:02  report    /work/itsdangerous/.repo-analyzer/report.html

Done in 6.8 min. Report: /work/itsdangerous/.repo-analyzer/report.html
```

The agent split the library into Signing (signer, signing algorithms, timestamp signer), Signed serialization
(serializer core, timed serializer, URL-safe serializers) and a Shared foundation (encoding helpers, error types,
package exports), with `tests/` as tests and docs, CI and packaging files excluded.

Exit codes: `0` finished, `1` could not run (the message says why), `130` interrupted.

## What it writes

Everything goes into one folder inside the project, `.repo-analyzer/`. Nothing else in the project is created,
changed or deleted. The folder contains a `.gitignore` with `*`, so git ignores it without touching the project's
own `.gitignore`. Delete the folder to remove every trace of the analysis.

| File | Contents |
|---|---|
| `report.html` | The results page: components, subcomponents, scores and the evidence behind every number |
| `score.json` | Every number in the report · `report.md`: the same as text |
| `map.final.json` | The map that was scored (the agent's map after deterministic completion) |
| `map.json`, `notes.md` | The agent's map and its notes between sessions |
| `task/` | What the agent reads: the map format, a folder overview, the exact piece names |
| `agent/` | The Omnigent agent definition used for the run (`config.yaml`, `instructions.md`) |
| `state.json` | Progress, so an interrupted run continues where it stopped |
| `run.log`, `events.jsonl` | The progress log, readable and as JSON lines |

## How it works

| Step | What happens |
|---|---|
| preflight | A one-word question to the model through Omnigent, to fail in seconds if Omnigent or Claude is not working |
| read | Every project file is listed and parsed with tree-sitter (one set of syntax rules for all languages); the agent's task files are written |
| agent | The mapping agent reads the code and writes `map.json`, over as many sessions as it needs within a budget |
| check | Problems in the map go back to the agent, up to three rounds |
| complete | Fixed rules settle whatever is left, so the map is always valid; every correction is listed in the report |
| score | The eleven metrics and the effort for every subcomponent, component and the whole project |
| report | `report.html` |

**The agent** is an Omnigent agent run non-interactively (`omni run <agent> --no-session -p ...`), so it never waits
for input. Its working directory is the project folder. It can read files, search with a few read-only shell
commands, run the map checker, and write exactly two files: the map and its notes. It is told never to ask
questions and to make the map complete early and refine it afterwards. A session lasts at most about 30 minutes;
sessions are chained, each continuing from the saved map and notes, until the agent reports the map complete.
The definition is generated per run from [`omnigent.py`](repo_analyzer/omnigent.py) (tools and policies) and
[`agent.py`](repo_analyzer/agent.py) (instructions); the copy used is in `.repo-analyzer/agent/`.

**The map** lists, for every subcomponent, the folders, files, functions or methods it owns; tests and excluded
files (documentation, generated or copied code, configuration) are listed separately. The format is in
[`repo_analyzer/docs/map-format.md`](repo_analyzer/docs/map-format.md).

## The score

Effort is counted in lines of work: one unit is the work of reading one line of typical code.

```
Effort = (own lines + outside lines) × complexity
       + noise lines
       + 50 × (files + hops + name clashes + outward effects + hidden flow)
       + (own lines + caller lines) × (1 − test protection)
```

| Kind of work | Metrics |
|---|---|
| Reading | own lines, outside lines (code it calls in other components), noise lines (other code between its pieces) |
| Finding | files, hops (rounds of discovering files), name clashes, outward effects (shared values it changes), hidden flow (decorators, calls chosen at run time, functions passed as values) |
| Understanding | complexity, from decisions, nesting, data dependencies, vocabulary and concurrency |
| Checking | caller lines (code that calls it), test protection (share of its code that tests reach) |

Effort has no upper limit; effort per 100 own lines compares parts of different sizes. Every rule is in
[`docs/effort-scoring.md`](docs/effort-scoring.md).

## Reliability

- **Continue after any stop.** Running the same command again continues from the step that was running; the agent
  picks up from its saved map and notes. A session left running by a closed terminal is stopped first.
- **Network or service outages** (for example a sleeping laptop) are retried with growing waits for up to an hour.
  On macOS, idle sleep is held off while a session runs.
- **A stuck session** (no progress for 20 minutes while using no CPU) is stopped and the next session continues.
- **An agent that cannot finish** still leads to a report: its map goes through the repair rounds, then
  deterministic completion; the report lists every correction and warning.
- **One run per project.** A second run of the same project is refused while one is active.
- **Stops only when it cannot run at all**: a missing or unreadable folder, Omnigent or Claude not working, or
  another run active. The message says what to do.

## Safety

- Nothing outside `.repo-analyzer/` is written; the agent's policies allow writing only `map.json` and `notes.md`.
- The agent's shell runs only read-only searches (`git ls-files`, `git grep`, `grep`, `ls`, `cat`, `head`, `tail`,
  `wc`) on paths inside the project, plus the map checker. Absolute paths, `..`, redirections and chained commands
  are refused.
- No project code is executed. The project's files are treated as data; symbolic links are not followed.
- The parsed-code cache is kept in `~/.cache/repo-analyzer/`, never inside the project.

## Configuration

Defaults are in [`repo_analyzer/config.py`](repo_analyzer/config.py):

| Setting | Default | Meaning |
|---|---|---|
| `AGENT_MODEL`, `AGENT_EFFORT` | `claude-opus-5-5[1m]`, `max` | The agent's model and reasoning effort |
| `AGENT_FILES_PER_SESSION`, `AGENT_MIN_SESSIONS`, `AGENT_MAX_SESSIONS` | 400, 4, 20 | The session budget |
| `AGENT_TRANSIENT_RETRY_MINUTES` | 60 | How long outages are retried |
| `AGENT_STALL_SECONDS` | 1200 | When a silent, idle session counts as stuck |
| `REPAIR_ROUNDS` | 3 | Times map problems go back to the agent |
| `MAX_FILE_BYTES` | 2,000,000 | Larger files are not analysed |
| `TRIP_COST` | 50 | The price of one finding step, in lines of work |

`REPO_ANALYZER_OMNI` can point at a specific `omni` executable.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

The tests cover file listing, parsing, map checking, deterministic completion, scoring, the report, the agent's
policies, and whole runs. Whole runs use a fake Omnigent ([`tests/fake_omni.py`](tests/fake_omni.py)) that can
finish, stop early, lose the network, fail, hang or be interrupted, so every recovery path is exercised without
calling a model.

## Project layout

```
repo_analyzer/
  cli.py           command line
  pipeline.py      the steps of a run, resuming and reporting
  workspace.py     the .repo-analyzer/ folder: state, lock, logs
  files.py         which files belong to the project
  reader.py        tree-sitter parsing: pieces, calls, writes, hidden flow, complexity counts
  contract.py      reading and checking component maps
  completion.py    deterministic completion of a map
  agent.py         the mapping agent's task, instructions and sessions
  omnigent.py      running Omnigent: agent definition, policies, progress, watchdog
  metrics.py       the eleven metrics and the effort
  report.py        report.md          html_report.py  report.html
  config.py        every setting and the shared syntax rules
  docs/map-format.md   the map format the agent follows
  web/             the report page's style and script
docs/              the scoring rules, a score check on datasette, notes on setup effort
tests/             the test suite and the fake Omnigent
```
