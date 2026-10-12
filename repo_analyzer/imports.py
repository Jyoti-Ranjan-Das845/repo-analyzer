"""Where each file's import lines point: the project files they load and the names they bring in.

Import lines are matched to project files by their paths, the same way for every language:

* A quoted path ('./lib/read', "util/strings.h", "github.com/x/app/internal/util") is a file path. One that starts
  with ./ or ../ (or is . or ..) is relative to the importing file's folder.
* An unquoted name (itsdangerous.signer, .signer, crate::parser, App\\Models\\User) is a module path whose parts
  are separated by dots, :: or backslashes. Leading dots, self:: and super:: make it relative.
* In `from a import b` style lines a/b is tried too, because b may be a module.

A path names the project file or folder it leads to from the importing file's folder, or else the one whose path
ends with the same parts: src/itsdangerous/signer.py for itsdangerous.signer, the folder internal/util for the Go
import github.com/x/app/internal/util. A path of one part only matches a folder with a main file or a file at the
top of the project, never a file deep inside it: `import json` is the standard library, not lib/types/json.js.
A folder stands for its main file (index.js, __init__.py, mod.rs, ...) when it has one, else for all its files in
the importing file's language (a Go or Java package).

An import line that matches no project file, and none of whose path parts is the name of a project folder (or of
the project itself), points outside the project: a standard or installed library.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from . import config as K
from .reader import Repo

_QUOTED_RE = re.compile(r"""'([^'\n]*)'|"([^"\n]*)"|`([^`\n]*)`|<([\w./\\-]+)>""")
_TOKEN_RE = re.compile(r"\.+(?:[A-Za-z_$][\w$]*(?:(?:\.|::|\\)[A-Za-z_$][\w$]*)*)?"
                       r"|[A-Za-z_$][\w$]*(?:(?:\.|::|\\)[A-Za-z_$][\w$]*)*")
_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")


class Spec(NamedTuple):
    """An import path: `up` folders up (None: not relative), then `parts`."""
    up: int | None
    parts: tuple[str, ...]
    # self:: and super:: count from the importing file's own module: for parser.rs that is the folder parser/
    # (where its submodules live), so super:: is the folder parser.rs is in
    from_module: bool = False


@dataclass
class Imports:
    files: dict[str, set[str]] = field(default_factory=dict)
    """file -> the project files its import lines point to"""
    names: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    """file -> a name an import line brings in -> the project files that line points to (empty: outside)"""
    words: dict[str, set[str]] = field(default_factory=dict)
    """file -> every name its import lines mention"""
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    """file -> a name an import line renames (`a as b`: b) -> the name it has where it is defined (a)"""


def module_name(path: str) -> str:
    """The name a file is imported by: its name without extension, or its folder's name for a main file."""
    base = posixpath.splitext(posixpath.basename(path))[0]
    if base in K.MODULE_MAIN_NAMES:
        return posixpath.basename(posixpath.dirname(path)) or base
    return base


def _quoted_spec(s: str) -> Spec | None:
    """'./lib/read' -> (0, ('lib', 'read')); '..' -> (1, ()); 'a/b' -> (None, ('a', 'b'))."""
    parts = [p for p in re.split(r"[/\\]+", s.strip()) if p]
    if not parts:
        return None
    if parts[0] not in (".", ".."):
        return Spec(None, tuple(parts))
    up = 0
    while parts and parts[0] in (".", ".."):
        up += parts.pop(0) == ".."
    return Spec(up, tuple(parts))


def _name_spec(s: str) -> Spec:
    """.signer -> (0, ('signer',)); ..a.b -> (1, ('a', 'b')); super::x -> (1, ('x',), from the module);
    crate::x and a.b are not relative."""
    dots = len(s) - len(s.lstrip("."))
    parts = [p for p in re.split(r"\.|::|\\", s[dots:]) if p]
    if parts and parts[0] == "crate":
        return Spec(None, tuple(parts[1:]))
    if parts and parts[0] in ("self", "super"):
        up = 0
        while parts and parts[0] in ("self", "super"):
            up += parts.pop(0) == "super"
        return Spec(up, tuple(parts), from_module=True)
    return Spec(dots - 1 if dots else None, tuple(parts))


def _specs(text: str) -> tuple[list[Spec], list[Spec], list[Spec], bool]:
    """The paths an import line names: (written paths, paths put together from `from a import b`, single names,
    whether they are module paths rather than quoted file paths). Single names (`import json`) are only tried when
    none of the paths leads to a project file."""
    quoted = [next(g for g in m.groups() if g is not None) for m in _QUOTED_RE.finditer(text)]
    if quoted:
        return [s for s in map(_quoted_spec, quoted) if s is not None], [], [], False
    tokens = [t for t in _TOKEN_RE.findall(text) if t not in K.IMPORT_KEYWORDS]
    written: list[Spec] = []
    joined: list[Spec] = []
    singles: list[Spec] = []
    for i, token in enumerate(tokens):
        spec = _name_spec(token)
        if spec.up is None and not spec.parts:
            continue
        (written if spec.up is not None or len(spec.parts) > 1 else singles).append(spec)
        for later in tokens[i + 1:]:              # from a import b: b may be the module a/b
            if _WORD_RE.fullmatch(later):
                joined.append(spec._replace(parts=spec.parts + (later,)))
    return written, joined, singles, True


_ALIAS_RE = re.compile(r"([A-Za-z_$][\w$.:]*)\s+as\s+([A-Za-z_$][\w$]*)")


def _aliases(text: str) -> dict[str, str]:
    """`from x import a as b`, `import { a as b }`, `use x::a as b`: the name b stands for a."""
    out = {}
    for m in _ALIAS_RE.finditer(text):
        original = re.split(r"\.|::", m.group(1))[-1]
        if original and original != m.group(2) and original not in K.IMPORT_KEYWORDS:
            out[m.group(2)] = original
    return out


class _Index:
    """The project's files and folders, for matching import paths."""

    def __init__(self, repo: Repo):
        self.root_name = posixpath.basename(repo.root.rstrip("/")).lower()
        self.lang = {p: f.language for p, f in repo.files.items()}
        self.paths = set(repo.files)
        self.by_noext: dict[str, list[str]] = defaultdict(list)       # path without extension -> files
        self.dir_files: dict[str, list[str]] = defaultdict(list)      # folder -> files directly in it
        folders: set[str] = set()
        for p in repo.files:
            self.by_noext[posixpath.splitext(p)[0]].append(p)
            d = posixpath.dirname(p)
            self.dir_files[d].append(p)
            while d and d not in folders:
                folders.add(d)
                d = posixpath.dirname(d)
        # last path part (lower case) -> (all parts, file path without extension or folder, is a folder)
        self.by_last: dict[str, list[tuple[tuple[str, ...], str, bool]]] = defaultdict(list)
        for noext in self.by_noext:
            parts = tuple(noext.lower().split("/"))
            self.by_last[parts[-1]].append((parts, noext, False))
        for d in folders:
            parts = tuple(d.lower().split("/"))
            self.by_last[parts[-1]].append((parts, d, True))
        self.folder_names = {d.rsplit("/", 1)[-1].lower() for d in folders} | {self.root_name}

    def main_files(self, folder: str) -> set[str]:
        return {f for f in self.dir_files.get(folder, ())
                if posixpath.splitext(posixpath.basename(f))[0] in K.MODULE_MAIN_NAMES}

    def folder(self, folder: str, lang: str | None) -> set[str]:
        """A folder stands for its main file, else for its files in the importing file's language."""
        main = self.main_files(folder)
        if main or lang is None:
            return main
        return {f for f in self.dir_files.get(folder, ()) if self.lang.get(f) == lang}

    def at(self, path: str, lang: str | None) -> set[str]:
        """The project files a path leads to: a file, with or without its extension, else a folder."""
        path = posixpath.normpath(path) if path else "."
        if path == ".":
            path = ""
        if path == ".." or path.startswith("../"):
            return set()
        if path in self.paths:
            return {path}
        return set(self.by_noext.get(path, ())) or self.folder(path, lang)

    def by_ending(self, parts: tuple[str, ...], lang: str | None) -> set[str]:
        """Project files or folders whose path ends with the same parts as the import path, or that the import path
        ends with (a Go import starts with the module's address). The longest match wins."""
        want = tuple(p.lower() for p in parts)
        best, found = 0, set()
        for cand, target, is_folder in self.by_last.get(want[-1], ()):
            k = 0
            while k < min(len(want), len(cand)) and want[-1 - k] == cand[-1 - k]:
                k += 1
            if len(want) == 1:
                ok = (is_folder and bool(self.main_files(target))) or len(cand) == 1
            else:
                ok = k >= 2 or k == len(cand)
            if not ok or k < best:
                continue
            files = self.folder(target, lang) if is_folder else set(self.by_noext[target])
            if files:
                if k > best:
                    best, found = k, set()
                found |= files
        return found

    def resolve(self, spec: Spec, importer: str, module_path: bool) -> set[str]:
        """The project files an import path leads to. When nothing matches, the path without its last part is
        tried (the last part may name something inside a module). A module path (a.b.c, not a quoted file path)
        also loads the main file of each folder it passes through: a package runs its __init__.py first."""
        parts = spec.parts
        for used in ((parts, parts[:-1]) if len(parts) > 1 else (parts,)):
            found = self._find(spec._replace(parts=used), importer)
            if found:
                return self._with_packages(found, used) if module_path else found
        return set()

    def _find(self, spec: Spec, importer: str) -> set[str]:
        up, parts = spec.up, spec.parts
        here = posixpath.dirname(importer)
        lang = self.lang.get(importer)
        if up is not None:
            base = here.split("/") if here else []
            stem = posixpath.splitext(posixpath.basename(importer))[0]
            if spec.from_module and stem not in K.MODULE_MAIN_NAMES:
                base.append(stem)                  # parser.rs: its own module is the folder parser/
            if up > len(base):
                return set()
            return self.at("/".join(base[:len(base) - up] + list(parts)), lang)
        if not parts:
            return set()
        if len(parts) == 1 and parts[0].lower() == self.root_name and self.main_files(""):
            return self.main_files("")            # the project imported by its own name
        return self.at(posixpath.join(here, *parts), lang) or self.by_ending(parts, lang)   # next to it first

    def _with_packages(self, files: set[str], parts: tuple[str, ...]) -> set[str]:
        out = set(files)
        for f in files:
            folder = posixpath.dirname(f)
            if posixpath.splitext(posixpath.basename(f))[0] in K.MODULE_MAIN_NAMES:
                folder = posixpath.dirname(folder)         # f is the main file of the folder the last part names
            for part in reversed(parts[:-1]):
                if not folder or posixpath.basename(folder).lower() != part.lower():
                    break
                out |= self.main_files(folder)
                folder = posixpath.dirname(folder)
        return out


def _inside(a: tuple, b: tuple) -> bool:
    """Import record a lies strictly inside record b (records start with their start and end byte)."""
    return a is not b and b[0] <= a[0] and a[1] <= b[1] and (a[0], a[1]) != (b[0], b[1])


def read_imports(repo: Repo) -> Imports:
    """Match every file's import lines to project files, and note the names each line brings in."""
    index = _Index(repo)
    out = Imports()
    for path, f in repo.files.items():
        records = []                              # (start, end, words, files, outside)
        aliases: dict[str, str] = {}
        for start, end, text in f.imports:
            written, joined, singles, module_path = _specs(text)
            if any(s <= start and end <= e and (s, e) != (start, end) for s, e, _ in f.imports):
                joined, singles = [], []          # a part of a bigger import line counts only for a path it writes
            else:
                aliases.update(_aliases(text))
            if not written and not joined and not singles:
                continue
            found: set[str] = set()
            for spec in written + joined:
                found |= index.resolve(spec, path, module_path)
            if not found:
                for spec in singles:
                    found |= index.resolve(spec, path, module_path)
            found.discard(path)
            specs = written + joined + singles
            outside = not found and all(s.up is None for s in specs) and not any(
                p.lower() in index.folder_names for s in specs for p in s.parts)
            words = {w for w in _WORD_RE.findall(text) if w not in K.IMPORT_KEYWORDS}
            records.append((start, end, words, found, outside))
        if aliases:
            out.aliases[path] = aliases
        if not records:
            continue
        # what a file imports is what its whole import lines point to; the parts of a line (Python's `.` in
        # `from .money import x`) only tell which name came from where
        whole = [r for r in records if not any(_inside(r, o) for o in records)]
        out.files[path] = set().union(*(r[3] for r in whole))
        out.words[path] = set().union(*(r[2] for r in records))
        names: dict[str, set[str]] = {}
        for word in out.words[path]:
            holders = [r for r in records if word in r[2]]
            # the innermost line that names it: a Go import spec rather than the whole import block
            inner = [r for r in holders if not any(_inside(o, r) for o in holders)]
            files = set().union(*(r[3] for r in inner))
            if files:
                names[word] = files
            elif all(r[4] for r in inner):
                names[word] = set()
        out.names[path] = names
    return out
