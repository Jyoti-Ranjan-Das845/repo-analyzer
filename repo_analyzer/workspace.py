"""The run folder: everything a run writes lives in <project>/.repo-analyzer/ and nowhere else in the project.

    .repo-analyzer/
      .gitignore      "*": git ignores the whole folder (the project's own .gitignore is never touched)
      README.md       what this folder is
      report.html     the final page: components, subcomponents, scores and the evidence behind them
      score.json      every number in the report      report.md   the same as text
      map.final.json  the map that was scored (the agent's map after deterministic completion)
      map.json        the agent's map                 notes.md    the agent's notes between sessions
      task/           what the agent reads: map-format.md, tree.md, pieces.md
      agent/          the Omnigent agent definition used for this run (config.yaml, instructions.md)
      state.json      progress, so an interrupted run continues where it stopped
      run.log         readable progress log               events.jsonl  the same, one JSON object per line
      run.lock        present while a run is active
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config as K

STEPS = ("preflight", "read", "agent", "check", "complete", "score", "report")

FOLDER_README = """# .repo-analyzer

Written by repo-analyzer (`repo-analyzer analyze <project>`). Open **report.html** for the results.

Everything the analysis writes is in this folder; nothing else in the project is changed. Git ignores this folder
(see `.gitignore` here). Delete the folder to remove every trace of the analysis; run the command again to continue
an interrupted analysis, or with `--fresh` to start over.
"""

# Only these names are ever removed by --fresh: files the tool itself creates.
OWNED_NAMES = ("report.html", "score.json", "report.md", "map.final.json", "map.json", "notes.md", "task", "agent",
               "state.json", "run.log", "events.jsonl")


class WorkspaceError(RuntimeError):
    pass


@dataclass
class State:
    project: str
    status: str = "new"                           # new | running | interrupted | failed | done
    steps: dict = field(default_factory=lambda: {s: {"status": "pending"} for s in STEPS})
    sessions: int = 0                             # agent sessions finished (kept across interruptions)
    repairs: int = 0
    budget: int = 0
    fingerprint: str = ""
    files: int = 0
    started: float = 0.0
    finished: float = 0.0
    error: str = ""
    warnings: list = field(default_factory=list)
    corrections: list = field(default_factory=list)
    agent_status: str = ""                        # complete | budget | failed | skipped


class Workspace:
    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.dir = self.project / K.WORK_DIR
        self.task_dir = self.dir / "task"
        self.agent_dir = self.dir / "agent"
        self.map_path = self.dir / "map.json"
        self.notes_path = self.dir / "notes.md"
        self.final_map_path = self.dir / "map.final.json"
        self.score_path = self.dir / "score.json"
        self.report_md_path = self.dir / "report.md"
        self.report_html_path = self.dir / "report.html"
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "run.log"
        self.events_path = self.dir / "events.jsonl"
        self.lock_path = self.dir / "run.lock"

    # ---------------------------------------------------------------- folder
    def create(self) -> None:
        try:
            self.dir.mkdir(exist_ok=True)
            (self.dir / ".gitignore").write_text("*\n")
            readme = self.dir / "README.md"
            if not readme.exists():
                readme.write_text(FOLDER_README)
            self.task_dir.mkdir(exist_ok=True)
            self.agent_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"cannot write {self.dir}: {exc.strerror or exc}") from exc

    def reset(self) -> None:
        """Start over: remove the files a previous run created (and nothing else)."""
        for name in OWNED_NAMES:
            path = self.dir / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()

    # ---------------------------------------------------------------- lock
    @contextmanager
    def lock(self):
        """Only one run per project at a time. A lock left by a run that no longer exists is taken over."""
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                pid = self._lock_pid()
                if pid and _alive(pid):
                    raise WorkspaceError(f"another analysis of this project is running (process {pid}); "
                                         f"wait for it, or stop it, before starting a new one")
                self.lock_path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            break
        else:
            raise WorkspaceError(f"could not take the lock {self.lock_path}")
        try:
            yield
        finally:
            if self._lock_pid() == os.getpid():
                self.lock_path.unlink(missing_ok=True)

    def _lock_pid(self) -> int | None:
        try:
            return int(self.lock_path.read_text().strip())
        except (OSError, ValueError):
            return None

    # ---------------------------------------------------------------- state
    def load_state(self) -> State | None:
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return None
        state = State(project=str(self.project))
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        for step in STEPS:
            state.steps.setdefault(step, {"status": "pending"})
        return state

    def save_state(self, state: State) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=1))
        os.replace(tmp, self.state_path)

    # ---------------------------------------------------------------- log
    def log(self, event: dict) -> None:
        event = {"t": round(time.time(), 3), **event}
        try:
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            line = event.get("line")
            if line:
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event['t']))}  {line}\n")
        except OSError:
            pass                                   # logging must never stop the run


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
