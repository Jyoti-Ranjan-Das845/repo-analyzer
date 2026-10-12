"""The whole analysis of one project folder.

    preflight  Omnigent and Claude answer (skipped when no agent work is left)
    read       every project file is listed and parsed; the agent's task files are written
    agent      the mapping agent writes the component map, over as many sessions as it needs (within a budget)
    check      problems in the map go back to the agent, a few rounds at most
    complete   deterministic completion fixes whatever is left, so the map is always valid
    score      the eleven metrics and the effort for every subcomponent, component and the whole project
    report     report.html, the page with all results

Every step is recorded in .repo-analyzer/state.json. Running the same command again continues an interrupted run
from the step it stopped at (the agent from its saved map and notes); --fresh starts over. Only problems that make
the run impossible stop it with an error: an unusable folder, Omnigent or Claude not working, another run active.
If the agent itself cannot finish, the run still ends with a report and says why.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config as K
from .agent import MappingAgent, prepare_task, session_budget
from .completion import complete, write_json
from .contract import check as check_map
from .files import git_commit
from .html_report import write_html_report
from .metrics import score
from .omnigent import AgentCancelled, AgentError, kill_orphan, preflight, remove_stale_temp, stop_all
from .reader import read_repo
from .report import render as render_markdown
from .workspace import STEPS, State, Workspace, WorkspaceError


class AnalysisError(RuntimeError):
    """The analysis cannot run at all; the message says why and what to do."""


class Interrupted(RuntimeError):
    pass


@dataclass
class Options:
    fresh: bool = False
    model: str = K.AGENT_MODEL
    effort: str = K.AGENT_EFFORT
    max_sessions: int | None = None
    preflight: bool = True
    quiet: bool = False


def validate_project(path: str | Path) -> Path:
    project = Path(path).expanduser()
    if not project.is_absolute():
        project = Path.cwd() / project
    project = project.resolve()
    if not project.exists():
        raise AnalysisError(f"no such folder: {project}")
    if not project.is_dir():
        raise AnalysisError(f"not a folder: {project}")
    if project == Path(project.anchor):
        raise AnalysisError("refusing to analyse the root of the file system; give a project folder")
    if '"' in str(project) or "\\" in str(project) or "\n" in str(project):
        raise AnalysisError(f"the folder path contains a quote, backslash or line break, which the agent's "
                            f"policies cannot express; move or rename the folder: {project}")
    return project


def analyze(project: str | Path, options: Options | None = None, out: Callable[[str], None] = print) -> Path:
    """Run (or continue) the analysis of a project folder. Returns the path of report.html."""
    options = options or Options()
    project = validate_project(project)
    ws = Workspace(project)
    try:
        ws.create()
        with ws.lock():
            if options.fresh:
                ws.reset()
                ws.create()
            state = ws.load_state()
            if state is None or state.project != str(project):
                state = State(project=str(project))
            if state.status == "done" and ws.report_html_path.exists():
                out(f"Already analysed. Report: {ws.report_html_path}")
                out("Run with --fresh to analyse again (for example after the code changed).")
                return ws.report_html_path
            return _Run(ws, state, options, out).run()
    except WorkspaceError as exc:
        raise AnalysisError(str(exc)) from exc


class _Run:
    def __init__(self, ws: Workspace, state: State, options: Options, out: Callable[[str], None]):
        self.ws, self.state, self.options, self.out = ws, state, options, out
        self.cancel = threading.Event()
        self.started = time.time()

    # ---------------------------------------------------------------- output
    def say(self, step: str, text: str) -> None:
        line = f"{step:<9} {text}"
        self.out(f"  {time.strftime('%H:%M:%S')}  {line}")
        self.ws.log({"type": "log", "step": step, "line": line})

    def agent_event(self, ev: dict) -> None:
        self.ws.log({"type": "agent", **ev, "line": f"agent     {ev.get('tool', ev['kind'])}  "
                                                    f"{ev.get('detail') or ev.get('text', '')}"})
        if self.options.quiet and ev["kind"] != "error":
            return
        if ev["kind"] == "tool":
            self.out(f"              {ev['tool']:<6} {ev['detail']}")
        elif ev["kind"] == "error":
            self.out(f"              ! {ev['text']}")
        elif ev["kind"] == "session":
            self.out(f"              {ev['text']}")
        elif ev["kind"] == "text":
            self.out(f"              · {ev['text'][:200]}")

    def warn(self, text: str) -> None:
        if text not in self.state.warnings:
            self.state.warnings.append(text)
        self.say("warning", text)

    @contextmanager
    def step(self, name: str):
        info = self.state.steps.setdefault(name, {})
        info.update(status="running", started=time.time())
        self.ws.save_state(self.state)
        try:
            yield info
        except BaseException:
            info["status"] = "failed"
            self.ws.save_state(self.state)
            raise
        info.update(status="done", finished=time.time())
        self.ws.save_state(self.state)

    def done(self, name: str) -> bool:
        return self.state.steps.get(name, {}).get("status") == "done"

    # ---------------------------------------------------------------- run
    def run(self) -> Path:
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def interrupt(signum, frame):
            if self.cancel.is_set():
                raise KeyboardInterrupt
            self.cancel.set()
            self.out("\n  Stopping… (press Ctrl+C again to stop at once)")

        if threading.current_thread() is threading.main_thread():
            for sig in previous:
                signal.signal(sig, interrupt)
        resumed = self.state.status in ("interrupted", "failed", "running")
        self.state.status, self.state.error = "running", ""
        self.state.started = self.state.started or self.started
        self.ws.save_state(self.state)
        self.out(f"repo-analyzer: {self.ws.project}" + ("  (continuing)" if resumed else ""))
        try:
            report = self._steps()
            self.state.status, self.state.finished = "done", time.time()
            self.ws.save_state(self.state)
            self.out(f"\nDone in {(time.time() - self.started) / 60:.1f} min. Report: {report}")
            return report
        except (Interrupted, AgentCancelled, KeyboardInterrupt):
            stop_all()
            self.state.status = "interrupted"
            self.ws.save_state(self.state)
            self.out("\nInterrupted. Run the same command again to continue where it stopped.")
            raise Interrupted() from None
        except AnalysisError as exc:
            self.state.status, self.state.error = "failed", str(exc)
            self.ws.save_state(self.state)
            raise
        except Exception as exc:                   # noqa: BLE001 - recorded, then shown to the user
            self.state.status, self.state.error = "failed", f"{type(exc).__name__}: {exc}"
            self.ws.save_state(self.state)
            self.ws.log({"type": "error", "line": f"error     {exc}", "trace": traceback.format_exc()})
            raise AnalysisError(f"unexpected error: {type(exc).__name__}: {exc} (details in {self.ws.log_path}); "
                                f"run the same command again to continue") from exc
        finally:
            stop_all()
            if threading.current_thread() is threading.main_thread():
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Interrupted()

    def _steps(self) -> Path:
        ws, state, opts = self.ws, self.state, self.options
        remove_stale_temp()
        if kill_orphan(ws.agent_dir):
            self.say("agent", "stopped a session an earlier run left behind")
        agent_work_left = not (self.done("agent") and self.done("check"))

        # preflight -----------------------------------------------------------------------------------------
        if opts.preflight and agent_work_left:
            with self.step("preflight"):
                try:
                    info = preflight(ws.agent_dir, opts.model)
                except AgentError as exc:
                    raise AnalysisError(f"Omnigent or Claude is not working: {exc}. Check that `omni` and `claude` "
                                        f"are installed and signed in, then run the command again.") from exc
                self.say("preflight", f"omni {info['omni_version']} · claude {info['claude_version']} · "
                                      f"{info['model']} answers")
        self._check_cancel()

        # read ----------------------------------------------------------------------------------------------
        with self.step("read"):
            repo = read_repo(str(ws.project))
            if not repo.files:
                raise AnalysisError(f"no text files to analyse in {ws.project}")
            name, commit = ws.project.name, git_commit(str(ws.project)) or ""
            prepare_task(ws, repo, name, commit)
            state.files = len(repo.files)
            state.budget = session_budget(repo, opts.max_sessions)
            pieces = sum(len(f.pieces) for f in repo.files.values())
            skipped = len(repo.skipped_large) + len(repo.skipped_binary)
            self.say("read", f"{len(repo.files):,} text files ({'git' if repo.source == 'git' else 'folder'}), "
                             f"{pieces:,} named pieces" + (f", {skipped:,} binary or large files skipped" if skipped else ""))
            if len(repo.files) > K.LARGE_PROJECT_FILES:
                self.warn(f"a large project ({len(repo.files):,} files): the agent may take several hours")
        self._check_cancel()

        agent = None
        if agent_work_left:
            agent = MappingAgent(ws, name, repo.source == "git", self.agent_event,
                                 lambda m: self.say("agent", m), self.cancel, opts.model, opts.effort)

        # agent ---------------------------------------------------------------------------------------------
        if not self.done("agent"):
            with self.step("agent"):
                def on_session(n: int) -> None:
                    state.sessions = n
                    ws.save_state(state)
                try:
                    finished = agent.map_project(state.budget, state.sessions, on_session)
                    state.agent_status = "complete" if finished else "budget"
                    if finished:
                        self.say("agent", f"the agent reports the map complete ({state.sessions} session(s))")
                    else:
                        self.warn(f"the agent used its {state.budget} sessions without reporting the map complete; "
                                  f"its map is completed deterministically")
                except AgentCancelled:
                    raise
                except AgentError as exc:
                    state.agent_status = "failed"
                    self.warn(f"the agent could not finish ({exc}); its map is completed deterministically")
        self._check_cancel()

        # check (+ repair rounds) ---------------------------------------------------------------------------
        if not self.done("check"):
            with self.step("check"):
                while True:
                    _, _, problems, _ = check_map(str(ws.map_path), repo)
                    if not problems:
                        self.say("check", "the map is complete and consistent")
                        break
                    self.say("check", f"{len(problems)} problem(s) in the map; first: {problems[0].message}")
                    if state.agent_status == "failed" or state.repairs >= K.REPAIR_ROUNDS or agent is None:
                        break
                    state.repairs += 1
                    ws.save_state(state)
                    try:
                        agent.repair([p.message for p in problems])
                    except AgentCancelled:
                        raise
                    except AgentError as exc:
                        self.warn(f"the agent could not repair the map ({exc})")
                        break
                    self._check_cancel()
        self._check_cancel()

        # complete ------------------------------------------------------------------------------------------
        with self.step("complete"):
            data, contract, res, corrections = complete(str(ws.map_path), repo, name, commit)
            write_json(str(ws.final_map_path), data)
            state.corrections = corrections
            self.say("complete", "no corrections needed" if not corrections
                     else f"{len(corrections)} correction(s): " + "; ".join(corrections))

        # score ---------------------------------------------------------------------------------------------
        with self.step("score"):
            result = score(repo, contract, res)
            result["run"] = self._run_info(repo, name, commit)
            write_json(str(ws.score_path), result)
            ws.report_md_path.write_text(render_markdown(result))
            rep = result["repository"]
            self.say("score", f"{len(contract.components)} components, {len(contract.subs)} subcomponents; "
                              f"effort {rep.get('effort', 0):,.0f}, {rep.get('per_100_lines', 0):,.0f} per 100 lines")

        # report --------------------------------------------------------------------------------------------
        with self.step("report"):
            write_html_report(ws.report_html_path, result, data)
            self.say("report", str(ws.report_html_path))
        return ws.report_html_path

    def _run_info(self, repo, name: str, commit: str) -> dict:
        s = self.state
        return {
            "project": str(self.ws.project), "name": name, "commit": commit,
            "listing": repo.source, "files": len(repo.files),
            "skipped_large": len(repo.skipped_large), "skipped_binary": len(repo.skipped_binary),
            "model": self.options.model, "effort": self.options.effort,
            "agent_status": s.agent_status or "skipped", "sessions": s.sessions, "repairs": s.repairs,
            "corrections": s.corrections, "warnings": s.warnings,
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "minutes": round((time.time() - (s.started or self.started)) / 60, 1),
        }


def check_only(project: str | Path, map_path: str | None, out: Callable[[str], None] = print) -> int:
    """Print every problem in a map (the agent runs this). Returns the exit code: 0 when the map is clean."""
    project = validate_project(project)
    ws = Workspace(project)
    repo = read_repo(str(project))
    _, res, problems, warnings = check_map(map_path or str(ws.map_path), repo)
    for w in warnings:
        out(f"warning: {w}")
    if problems:
        out(f"{len(problems)} problem(s) in the map:")
        for p in problems[:300]:
            out(f"  - {p.message}")
        if len(problems) > 300:
            out(f"  ... and {len(problems) - 300} more")
        return 1
    owned: dict[str, int] = {}
    for path, owners in res.owner.items():
        for o, nb in zip(owners, repo.files[path].nonblank):
            if nb and o:
                owned[o] = owned.get(o, 0) + 1
    out("OK: the map is complete and consistent.")
    out(f"{len(repo.files):,} text files; lines per owner: "
        + ", ".join(f"{k.strip('_')} {v:,}" for k, v in sorted(owned.items())))
    return 0


def score_only(project: str | Path, map_path: str | None, out: Callable[[str], None] = print) -> Path:
    """Complete, score and report an existing map without running the agent (for example after editing it)."""
    project = validate_project(project)
    ws = Workspace(project)
    ws.create()
    with ws.lock():
        repo = read_repo(str(project))
        name, commit = project.name, git_commit(str(project)) or ""
        data, contract, res, corrections = complete(map_path or str(ws.map_path), repo, name, commit)
        write_json(str(ws.final_map_path), data)
        result = score(repo, contract, res)
        result["run"] = {"project": str(project), "name": name, "commit": commit, "listing": repo.source,
                         "files": len(repo.files), "skipped_large": len(repo.skipped_large),
                         "skipped_binary": len(repo.skipped_binary), "agent_status": "not run",
                         "sessions": 0, "repairs": 0, "corrections": corrections, "warnings": [],
                         "generated": time.strftime("%Y-%m-%d %H:%M"), "model": "", "effort": "", "minutes": 0}
        write_json(str(ws.score_path), result)
        ws.report_md_path.write_text(render_markdown(result))
        write_html_report(ws.report_html_path, result, data)
    for c in corrections:
        out(f"corrected: {c}")
    out(f"Report: {ws.report_html_path}")
    return ws.report_html_path
