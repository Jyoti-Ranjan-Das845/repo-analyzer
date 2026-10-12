"""Command line.

  repo-analyzer analyze <project folder>     the whole analysis; ends with <project>/.repo-analyzer/report.html
  repo-analyzer check   <project folder>     check the component map (the agent runs this while it works)
  repo-analyzer score   <project folder>     complete, score and report the current map without running the agent
  repo-analyzer pieces  <project folder>     print every file's named pieces (what map entries may name)

(`python -m repo_analyzer ...` works the same.)
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import config as K
from .pipeline import AnalysisError, Interrupted, Options, analyze, check_only, score_only, validate_project
from .reader import pieces_listing, read_repo


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):           # progress shows line by line even when written to a file
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="repo-analyzer",
        description="Score how much work it takes to make a safe change in each part of a codebase.")
    ap.add_argument("--version", action="version", version=f"repo-analyzer {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    p = sub.add_parser("analyze", help="analyse a project folder (resumes an interrupted run)",
                       description="Analyse a project folder. Everything is written to <project>/.repo-analyzer/; "
                                   "the result is report.html there. Running it again continues an interrupted run.")
    p.add_argument("project", help="path to the project folder")
    p.add_argument("--fresh", action="store_true", help="start over instead of continuing an earlier run")
    p.add_argument("--model", default=K.AGENT_MODEL, help=f"model for the mapping agent (default {K.AGENT_MODEL})")
    p.add_argument("--effort", default=K.AGENT_EFFORT, choices=("low", "medium", "high", "xhigh", "max"),
                   help=f"the agent's reasoning effort (default {K.AGENT_EFFORT})")
    p.add_argument("--max-sessions", type=int, default=None, metavar="N",
                   help="at most N agent sessions of about 30 minutes (default: scaled to the project's size)")
    p.add_argument("--skip-preflight", action="store_true", help="do not check Omnigent and Claude before starting")
    p.add_argument("--quiet", action="store_true", help="do not print every file the agent opens")

    p = sub.add_parser("check", help="check a component map")
    p.add_argument("project", help="path to the project folder")
    p.add_argument("--map", help=f"the map (default <project>/{K.WORK_DIR}/map.json)")

    p = sub.add_parser("score", help="complete, score and report the current map without running the agent")
    p.add_argument("project", help="path to the project folder")
    p.add_argument("--map", help=f"the map (default <project>/{K.WORK_DIR}/map.json)")

    p = sub.add_parser("pieces", help="print every file's named pieces")
    p.add_argument("project", help="path to the project folder")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "analyze":
            if args.max_sessions is not None and args.max_sessions < 1:
                ap.error("--max-sessions must be at least 1")
            analyze(args.project, Options(fresh=args.fresh, model=args.model, effort=args.effort,
                                          max_sessions=args.max_sessions, preflight=not args.skip_preflight,
                                          quiet=args.quiet))
            return 0
        if args.cmd == "check":
            return check_only(args.project, args.map)
        if args.cmd == "score":
            score_only(args.project, args.map)
            return 0
        if args.cmd == "pieces":
            sys.stdout.write(pieces_listing(read_repo(str(validate_project(args.project)))))
            return 0
    except Interrupted:
        return 130
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
