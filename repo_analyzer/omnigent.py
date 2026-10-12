"""Running the mapping agent through Omnigent, non-interactively.

The agent definition (a bundle folder with config.yaml + instructions.md) is written into the run folder:
- the claude-sdk harness, with the model and reasoning effort from config.py; no host skills or project settings;
- os_env file tools rooted at the project folder (Omnigent confines them to it): read, write, edit and a shell;
- policies: the shell may run only read-only search commands on relative paths, or the exact checker command;
  files may be written only at the map and notes paths; every other tool is denied.

`omni run <bundle> --no-session -p <kickoff>` runs with the project folder as its working directory, so it never
waits for input: one prompt in, one final answer out (stdout). Progress is read from the session's local
database ($TMPDIR/ap-chat-data-*/chat.db), which Omnigent writes as the agent works. Omnigent follows a -p run
for about 32 minutes, so callers chain sessions for long tasks (see agent.py).
"""

from __future__ import annotations

import atexit
import glob
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import config as K

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
PID_FILE = "session.pid"
TEMP_PREFIX = "repo-analyzer-"


class AgentError(RuntimeError):
    """A session failed."""


class AgentUnavailable(AgentError):
    """The session failed because the network or the model service could not be reached, or it stalled: worth
    retrying after a wait."""


class AgentCancelled(AgentError):
    """The run was interrupted (Ctrl+C or a stop signal)."""


# --------------------------------------------------------------------------------------------------------------
# executables

def find_omni() -> str:
    path = os.environ.get(K.OMNI_PATH_ENV) or shutil.which("omni") or os.path.expanduser("~/.local/bin/omni")
    if not path or not os.path.exists(path):
        raise AgentError("Omnigent is not installed: the `omni` command was not found "
                         f"(install Omnigent, or set {K.OMNI_PATH_ENV} to its path)")
    return path


def find_claude() -> str | None:
    """The newest Claude CLI on this machine (Omnigent's claude-sdk harness drives it)."""
    if K.AGENT_CLAUDE_PATH:
        return K.AGENT_CLAUDE_PATH
    candidates = {p for p in [shutil.which("claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude",
                              *glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")),
                              os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude")]
                  if p and os.path.exists(p)}
    best, best_v = None, (-1, -1, -1)
    for p in sorted(candidates):
        try:
            out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        m = _VERSION_RE.search(out or "")
        if m and tuple(map(int, m.groups())) > best_v:
            best, best_v = p, tuple(map(int, m.groups()))
    return best


def version_of(executable: str) -> str:
    try:
        out = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    m = _VERSION_RE.search(out.stdout or out.stderr or "")
    return m.group(0) if m else "unknown"


# --------------------------------------------------------------------------------------------------------------
# the agent definition

def checker_command(project: Path, map_path: Path) -> str:
    """The exact command the agent may run to check its map (it runs this program, never project code)."""
    return " ".join(shlex.quote(str(x)) for x in
                    (sys.executable, "-I", "-m", "repo_analyzer", "check", project, "--map", map_path))


def _cel(value: str) -> str:
    if any(c in value for c in '"\\\n'):
        raise AgentError(f"a path with quotes, backslashes or line breaks cannot be used: {value!r}")
    return f'"{value}"'


_SHELL_ARG = r"(\s+(\x22[^\x22$`\\]*\x22|\x27[^\x27]*\x27|[^\s;&|`$<>(){}\\\x22\x27])+)*"
SHELL_ALLOW_RE = (r"^\s*(git\s+(ls-files|grep|log|show)|grep|ls|cat|head|tail|wc)" + _SHELL_ARG
                  + r"(\s*\|\s*(grep|head|tail|wc|uniq|cut|sort)" + _SHELL_ARG + r")*\s*$")
SHELL_DENY_RE = r"(^|[\s\x22\x27=])[/~]|\.\.|--output|--open-files-in-pager|\s-O|\bsort\b[^|]*\s-o"


def write_bundle(bundle: Path, instructions: str, project: Path, writable: list[Path], checker: str,
                 model: str = K.AGENT_MODEL, effort: str = K.AGENT_EFFORT) -> None:
    """The Omnigent agent definition for one run."""
    bundle.mkdir(parents=True, exist_ok=True)
    allowed_writes = []
    for p in writable:
        rel = p.relative_to(project).as_posix()
        allowed_writes += [str(p), rel, f"./{rel}"]
    writes = "[" + ", ".join(_cel(w) for w in allowed_writes) + "]"
    config = f"""spec_version: 1
name: repo_analyzer_mapper
description: Reads a project folder and writes its component map. Never asks questions; works on its own.
executor:
  type: omnigent
  model: {model}
  reasoning_effort: {effort}
  config:
    harness: claude-sdk
    permission_mode: auto
skills: none
async: false
os_env:
  type: caller_process
  cwd: .
  sandbox:
    type: none
guardrails:
  ask_timeout: 5
  policies:
    shell_read_only:
      type: function
      function:
        path: omnigent.policies.builtins.cel.cel_policy
        arguments:
          reason: >-
            The shell may run only read-only search commands (git ls-files, git grep, git log, git show, grep,
            ls, cat, head, tail, wc, piped into grep, head, tail, wc, uniq, cut or sort) on relative paths
            inside the project, with single-quoted patterns, or the exact checker command from your instructions.
          expression: >-
            event.type == "tool_call" && event.data.name.endsWith("sys_os_shell")
            ? ((event.data.arguments.command == {_cel(checker)}
                || (!event.data.arguments.command.matches(r"{SHELL_DENY_RE}")
                    && event.data.arguments.command.matches(r"{SHELL_ALLOW_RE}")))
               ? {{"result": "ALLOW"}}
               : {{"result": "DENY"}})
            : {{"result": "ALLOW"}}
    map_and_notes_only:
      type: function
      function:
        path: omnigent.policies.builtins.cel.cel_policy
        arguments:
          reason: The only files you may create or change are the map and the notes file named in your instructions.
          expression: >-
            event.type == "tool_call"
            && (event.data.name.endsWith("sys_os_write") || event.data.name.endsWith("sys_os_edit"))
            ? (event.data.arguments.path in {writes} ? {{"result": "ALLOW"}} : {{"result": "DENY"}})
            : {{"result": "ALLOW"}}
    file_tools_only:
      type: function
      function:
        path: omnigent.policies.builtins.cel.cel_policy
        arguments:
          reason: Only sys_os_read, sys_os_write, sys_os_edit and sys_os_shell are available.
          expression: >-
            event.type == "tool_call"
            && (((event.data.name.startsWith("sys_") || event.data.name.startsWith("mcp__omnigent__"))
                 && !(event.data.name.endsWith("sys_os_read") || event.data.name.endsWith("sys_os_write")
                      || event.data.name.endsWith("sys_os_edit") || event.data.name.endsWith("sys_os_shell")))
                || (event.data.name.startsWith("mcp__") && !event.data.name.startsWith("mcp__omnigent__")))
            ? {{"result": "DENY"}}
            : {{"result": "ALLOW"}}
instructions: instructions.md
"""
    (bundle / "config.yaml").write_text(config)
    (bundle / "instructions.md").write_text(instructions)


def write_probe_bundle(bundle: Path, model: str) -> None:
    """A tool-less agent for the preflight check."""
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "config.yaml").write_text(f"""spec_version: 1
name: repo_analyzer_probe
description: Answers one word to show that the model can be reached.
executor:
  type: omnigent
  model: {model}
  reasoning_effort: low
  config:
    harness: claude-sdk
    permission_mode: auto
skills: none
async: false
instructions: instructions.md
""")
    (bundle / "instructions.md").write_text("Answer with exactly the word READY and nothing else.\n")


# --------------------------------------------------------------------------------------------------------------
# progress from the session database

def _short(text: str, n: int = 240) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def events_from_item(data: dict) -> list[dict]:
    """One stored conversation item -> progress events.

    Omnigent stores every tool call twice: once as the model's request (agent = "resp_<id>") and once as the
    runner's execution (agent = the bundle's name). Only the runner's record is shown. ToolSearch calls are the
    harness loading its tools, not work on the project, so they are skipped."""
    out = []
    if data.get("role") == "assistant" and isinstance(data.get("content"), list):
        for part in data["content"]:
            if isinstance(part, dict) and part.get("type") in ("output_text", "text") and str(part.get("text", "")).strip():
                out.append({"kind": "text", "text": _short(part["text"], 400)})
        return out
    name = data.get("name")
    if not name or "arguments" not in data or str(data.get("agent", "")).startswith("resp_") or name == "ToolSearch":
        return out
    args = data.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"value": args}
    tool = str(name).replace("mcp__omnigent__", "").replace("sys_os_", "")
    detail = ""
    if isinstance(args, dict):
        detail = args.get("path") or args.get("command") or args.get("pattern") or ""
        if not detail and args:
            detail = json.dumps(args)[:200]
    out.append({"kind": "tool", "tool": tool, "detail": _short(detail, 300)})
    return out


class _Progress(threading.Thread):
    """Reads new conversation items every 2 seconds and reports them; remembers when the last one arrived."""

    def __init__(self, tmpdir: Path, on_event: Callable[[dict], None], cwd: Path):
        super().__init__(daemon=True)
        self.tmpdir, self.on_event = tmpdir, on_event
        self.prefixes = sorted({str(cwd) + os.sep, str(Path(cwd).resolve()) + os.sep}, key=len, reverse=True)
        self.stop = threading.Event()
        self.seen = 0
        self.tool_calls = 0
        self.last_error = ""
        self.last_activity = time.time()

    def run(self) -> None:
        while not self.stop.wait(2.0):
            self.poll()
        self.poll()

    def poll(self) -> None:
        dbs = sorted(glob.glob(str(self.tmpdir / "ap-chat-data-*" / "chat.db")))
        if not dbs:
            return
        try:
            con = sqlite3.connect(f"file:{dbs[-1]}?mode=ro", uri=True, timeout=1)
            rows = con.execute("select position, data from conversation_items where position >= ? order by position",
                               (self.seen,)).fetchall()
            con.close()
        except sqlite3.Error:
            return
        for pos, raw in rows:
            self.seen = max(self.seen, pos + 1)
            self.last_activity = time.time()
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("source") == "harness" and data.get("message"):
                self.last_error = str(data["message"])
                self.on_event({"kind": "error", "text": _short(self.last_error, 300)})
                continue
            for ev in events_from_item(data):
                if ev["kind"] == "tool":
                    self.tool_calls += 1
                    for prefix in self.prefixes:          # paths relative to the project
                        ev["detail"] = ev["detail"].replace(prefix, "")
                self.on_event(ev)


# --------------------------------------------------------------------------------------------------------------
# process control

_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()


def _kill(proc: subprocess.Popen) -> None:
    """Stop the omni process and everything it started (it runs in its own process group)."""
    if proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def stop_all() -> None:
    """No agent session outlives this program."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        _kill(proc)


atexit.register(stop_all)


def _cpu_seconds(pgid: int) -> float | None:
    """CPU time used so far by every process in a process group (None if it cannot be read)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pgid=,time="], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    total = 0.0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[0] != str(pgid):
            continue
        clock = parts[1]
        days = 0
        if "-" in clock:
            d, clock = clock.split("-", 1)
            days = int(d)
        secs = 0.0
        for piece in clock.split(":"):
            secs = secs * 60 + float(piece)
        total += days * 86400 + secs
    return total


def kill_orphan(agent_dir: Path) -> bool:
    """Stop a session left running by a run that ended without stopping it (for example a closed terminal)."""
    pid_file = agent_dir / PID_FILE
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        cmd = ""
    killed = False
    if "omni" in cmd and str(agent_dir) in cmd:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
                killed = True
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(3)
    pid_file.unlink(missing_ok=True)
    return killed


def remove_stale_temp() -> None:
    """Temp folders of sessions whose program is gone (each folder name carries the owning process id)."""
    for path in glob.glob(os.path.join(tempfile.gettempdir(), TEMP_PREFIX + "*")):
        m = re.match(re.escape(TEMP_PREFIX) + r"(\d+)-", os.path.basename(path))
        if not m or not os.path.isdir(path):
            continue
        try:
            os.kill(int(m.group(1)), 0)
        except ProcessLookupError:
            shutil.rmtree(path, ignore_errors=True)
        except PermissionError:
            pass


def _transient(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in K.TRANSIENT_ERROR_HINTS)


# --------------------------------------------------------------------------------------------------------------
# one session

def run_session(bundle: Path, kickoff: str, cwd: Path, on_event: Callable[[dict], None],
                cancel: threading.Event | None = None, model: str = K.AGENT_MODEL,
                timeout: float | None = None, stall: float | None = None) -> str:
    """Run one non-interactive session and return its final answer.

    Raises AgentCancelled when `cancel` is set, AgentUnavailable when the model service or network failed or the
    session stalled (no new step while its processes use no CPU), AgentError for any other failure."""
    timeout = K.AGENT_SESSION_TIMEOUT_SECONDS if timeout is None else timeout
    stall = K.AGENT_STALL_SECONDS if stall is None else stall
    omni = find_omni()
    claude = find_claude()
    # A private temp folder: the session's database lands here (read for progress) and is removed afterwards.
    # It sits under the system temp root so the socket paths Omnigent creates in it stay short.
    tmpdir = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{os.getpid()}-"))
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    if claude:
        env["OMNIGENT_CLAUDE_PATH"] = claude
    cmd = [omni, "run", str(bundle), "--no-session", "--harness", "claude-sdk", "--model", model, "-p", kickoff]
    progress = _Progress(tmpdir, on_event, cwd)
    started = time.time()
    pid_file = bundle / PID_FILE
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise AgentError(f"could not start Omnigent: {exc}") from exc
    pid_file.write_text(str(proc.pid))
    with _ACTIVE_LOCK:
        _ACTIVE.add(proc)
    if K.AGENT_PREVENT_IDLE_SLEEP and shutil.which("caffeinate"):
        # macOS: no idle sleep while the session runs (caffeinate exits with the session)
        subprocess.Popen(["caffeinate", "-i", "-w", str(proc.pid)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    progress.start()
    cpu_mark = (time.time(), _cpu_seconds(proc.pid))
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=2)
                break
            except subprocess.TimeoutExpired:
                now = time.time()
                if cancel is not None and cancel.is_set():
                    _kill(proc)
                    raise AgentCancelled("interrupted")
                if now - started > timeout:
                    _kill(proc)
                    raise AgentUnavailable(f"the session did not finish within {timeout / 60:.0f} minutes")
                if now - cpu_mark[0] >= K.AGENT_STALL_CHECK_SECONDS:
                    cpu_now = _cpu_seconds(proc.pid)
                    idle = (cpu_mark[1] is not None and cpu_now is not None
                            and cpu_now - cpu_mark[1] < K.AGENT_STALL_CPU_SECONDS)
                    if idle and now - progress.last_activity > stall:
                        _kill(proc)
                        raise AgentUnavailable(f"the session stalled: no progress for "
                                               f"{(now - progress.last_activity) / 60:.0f} minutes and no CPU use")
                    cpu_mark = (now, cpu_now)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(proc)
        pid_file.unlink(missing_ok=True)
        progress.stop.set()
        progress.join(timeout=10)
        shutil.rmtree(tmpdir, ignore_errors=True)
    minutes = (time.time() - started) / 60
    on_event({"kind": "session", "text": f"session ended after {minutes:.1f} min and {progress.tool_calls} tool calls"})
    if proc.returncode != 0:
        detail = _short(err or out, 600)
        cls = AgentUnavailable if _transient(detail) or _transient(progress.last_error) else AgentError
        raise cls(f"the session failed (exit {proc.returncode}): {detail or progress.last_error}")
    answer = (out or "").strip()
    if progress.last_error and _transient(progress.last_error) and K.AGENT_DONE_MARKER not in answer:
        raise AgentUnavailable(f"the session stopped: {progress.last_error}")
    return answer


def preflight(work_dir: Path, model: str) -> dict:
    """Check, in under a minute or two, that Omnigent and Claude work, before any long work starts."""
    omni = find_omni()
    claude = find_claude()
    if not claude:
        raise AgentError("the Claude CLI (`claude`) was not found; Omnigent's claude-sdk harness needs it")
    info = {"omni": omni, "omni_version": version_of(omni), "claude": claude, "claude_version": version_of(claude),
            "model": model}
    bundle = work_dir / "preflight"
    write_probe_bundle(bundle, model)
    answer = run_session(bundle, "Answer now.", work_dir, lambda e: None, model=model,
                         timeout=K.PREFLIGHT_TIMEOUT_SECONDS, stall=K.PREFLIGHT_TIMEOUT_SECONDS)
    if "READY" not in answer.upper():
        raise AgentError(f"the model did not answer as expected (got: {_short(answer, 200) or 'nothing'})")
    return info
