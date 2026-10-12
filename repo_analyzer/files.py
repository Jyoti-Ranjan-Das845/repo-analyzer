"""Which files belong to the project.

Inside a git checkout, git decides: tracked files plus untracked files that are not ignored
(`git ls-files --cached --others --exclude-standard`). Anywhere else the folder is walked: every .gitignore on the
way is honoured and well-known dependency, cache and build folders (config.SKIP_DIRS) are skipped. In both cases
the run's own folder, symbolic links and files above config.MAX_FILE_BYTES are left out. Nothing is written.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field

import pathspec

from . import config as K

_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


@dataclass
class Listing:
    root: str
    files: list[str]                                   # relative '/'-separated paths, sorted
    source: str                                        # "git" or "folder"
    skipped_large: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        """Changes whenever a listed file is added, removed or modified (size or modification time)."""
        h = hashlib.sha256(self.source.encode())
        for path in self.files:
            try:
                st = os.stat(os.path.join(self.root, path))
                h.update(f"{path}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
            except OSError:
                h.update(f"{path}\0missing\n".encode("utf-8", "surrogateescape"))
        return h.hexdigest()


def _git(root: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, timeout=timeout,
                              env={**os.environ, **_GIT_ENV})
    except (OSError, subprocess.SubprocessError):
        return None


def in_git_checkout(root: str) -> bool:
    proc = _git(root, "rev-parse", "--is-inside-work-tree", timeout=60)
    return proc is not None and proc.returncode == 0 and proc.stdout.strip() == b"true"


def git_commit(root: str) -> str | None:
    """The checked-out commit, or None outside a git checkout (or without git)."""
    proc = _git(root, "rev-parse", "HEAD", timeout=60)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.decode().strip() or None


def _git_files(root: str) -> list[str] | None:
    if not in_git_checkout(root):
        return None
    proc = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".")
    if proc is None or proc.returncode != 0:
        return None
    return [os.fsdecode(raw) for raw in proc.stdout.split(b"\0") if raw]


def _walk_files(root: str) -> list[str]:
    specs: dict[str, pathspec.GitIgnoreSpec] = {}

    def ignored(rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        for depth in range(len(parts)):
            base = "/".join(parts[:depth])
            spec = specs.get(base)
            if spec is None:
                continue
            sub = "/".join(parts[depth:]) + ("/" if is_dir else "")
            if spec.match_file(sub):
                return True
        return False

    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        gitignore = os.path.join(dirpath, ".gitignore")
        if os.path.isfile(gitignore):
            try:
                with open(gitignore, encoding="utf-8", errors="replace") as fh:
                    specs[rel_dir] = pathspec.GitIgnoreSpec.from_lines(fh.read().splitlines())
            except OSError:
                pass
        keep = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if d in K.SKIP_DIRS or os.path.islink(os.path.join(dirpath, d)) or ignored(rel, True):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name not in K.SKIP_DIRS and not ignored(rel, False):
                out.append(rel)
    return out


def list_project_files(root: str) -> Listing:
    root = os.path.abspath(root)
    names = _git_files(root)
    source = "git"
    if names is None:
        names, source = _walk_files(root), "folder"
    files, large = [], []
    for rel in sorted(set(names)):
        if rel == K.WORK_DIR or rel.startswith(K.WORK_DIR + "/") or "/.git/" in f"/{rel}":
            continue
        full = os.path.join(root, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            continue                               # symbolic links may point outside the project
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size > K.MAX_FILE_BYTES:
            large.append(rel)
            continue
        files.append(rel)
    return Listing(root, files, source, large)
