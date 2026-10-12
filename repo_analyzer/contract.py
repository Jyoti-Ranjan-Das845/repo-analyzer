"""The component map (the contract the agent fills in): read it, work out which owner every line belongs to, and
report every problem in a structured way, so that it can be shown to the agent or fixed by completion.py.

Owners are subcomponent ids, plus TESTS and EXCLUDED for the project-level lists.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

from .reader import Repo

KINDS = ("feature", "foundation", "supporting")
TESTS = "__tests__"
EXCLUDED = "__excluded__"
_RANGE_RE = re.compile(r"^(?P<path>.+)#L(?P<lo>\d+)-L?(?P<hi>\d+)$")


@dataclass
class Sub:
    id: str
    name: str
    description: str
    component: str
    owns: list[str]


@dataclass
class Comp:
    id: str
    name: str
    kind: str
    description: str
    subs: list[Sub]


@dataclass
class Contract:
    repo: str
    commit: str
    tests: list[str]
    excluded: list[tuple[str, str]]
    components: list[Comp]

    @property
    def subs(self) -> list[Sub]:
        return [s for c in self.components for s in c.subs]


@dataclass
class Problem:
    """One thing wrong with a map.

    kind: structure | bad_entry | conflict | uncovered_file | unassigned_piece | empty_owner
    owner/entry name the entry at fault (bad_entry, conflict: the entry that lost), path/piece the code at fault."""
    kind: str
    message: str
    owner: str | None = None
    entry: str | None = None
    path: str | None = None
    piece: str | None = None


@dataclass
class Entry:
    raw: str
    owner: str
    kind: str                   # folder | file | piece | range
    path: str
    piece: str | None = None
    lo: int = 0
    hi: int = 0

    @property
    def rank(self) -> tuple[int, int]:
        """Higher wins: a line range beats a piece, a deeper piece beats its class, a piece beats a file,
        a file beats a folder, a deeper folder beats a shallower one."""
        if self.kind == "range":
            return (4, 0)
        if self.kind == "piece":
            return (3, self.piece.count(".") + 1)
        if self.kind == "file":
            return (2, 0)
        return (1, self.path.count("/") + 1 if self.path else 0)


@dataclass
class Resolution:
    owner: dict[str, list[str | None]]          # path -> owner of every line (index 0 = line 1)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [p.message for p in self.problems]


def owner_label(owner: str) -> str:
    return {TESTS: "tests", EXCLUDED: "excluded"}.get(owner, owner)


# --------------------------------------------------------------------------------------------------------------
# reading a map

def read_json(path: str) -> tuple[object | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, f"no map at {path}"
    except (OSError, ValueError) as exc:
        return None, f"the map is not valid JSON: {exc}"


def from_dict(data: object) -> tuple[Contract | None, list[Problem]]:
    """A Contract from parsed JSON, plus every structural problem found on the way."""
    problems: list[Problem] = []

    def bad(message: str) -> None:
        problems.append(Problem("structure", message))

    if not isinstance(data, dict):
        return None, [Problem("structure", "the map must be a JSON object")]

    def text(obj, key, where) -> str:
        val = obj.get(key)
        if not isinstance(val, str) or not val.strip():
            bad(f"{where}: '{key}' must be a non-empty string")
            return ""
        return val.strip()

    def str_list(obj, key, where) -> list[str]:
        val = obj.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            bad(f"{where}: '{key}' must be a list of strings")
            return [v.strip() for v in val if isinstance(v, str)] if isinstance(val, list) else []
        return [v.strip() for v in val]

    excluded = []
    raw_excluded = data.get("excluded", [])
    for i, item in enumerate(raw_excluded if isinstance(raw_excluded, list) else []):
        if isinstance(item, str):
            excluded.append((item.strip(), ""))
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            excluded.append((item["path"].strip(), str(item.get("reason", ""))))
        else:
            bad(f"excluded[{i}]: must be a path or {{\"path\": ..., \"reason\": ...}}")
    comps = []
    raw_comps = data.get("components", [])
    if not isinstance(raw_comps, list):
        bad("'components' must be a list")
        raw_comps = []
    for ci, c in enumerate(raw_comps):
        if not isinstance(c, dict):
            bad(f"components[{ci}] must be an object")
            continue
        where = f"component {c.get('id', ci)}"
        subs = []
        raw_subs = c.get("subcomponents", [])
        for si, s in enumerate(raw_subs if isinstance(raw_subs, list) else []):
            if not isinstance(s, dict):
                bad(f"{where}: subcomponents[{si}] must be an object")
                continue
            swhere = f"subcomponent {s.get('id', si)}"
            subs.append(Sub(text(s, "id", swhere), text(s, "name", swhere), text(s, "description", swhere),
                            str(c.get("id", "")), str_list(s, "owns", swhere)))
        comp = Comp(text(c, "id", where), text(c, "name", where), text(c, "kind", where),
                    text(c, "description", where), subs)
        if comp.kind and comp.kind not in KINDS:
            bad(f"{where}: kind must be one of {', '.join(KINDS)}")
        if not subs:
            bad(f"{where}: needs at least one subcomponent")
        comps.append(comp)
    if not comps:
        bad("the map has no components")
    contract = Contract(str(data.get("repo", "")), str(data.get("commit", "")),
                        str_list(data, "tests", "tests"), excluded, comps)
    ids = Counter([c.id for c in comps] + [s.id for s in contract.subs])
    for dup, n in ids.items():
        if dup and n > 1:
            bad(f"id '{dup}' is used {n} times")
    return contract, problems


def load(path: str) -> tuple[Contract | None, list[Problem]]:
    data, error = read_json(path)
    if error:
        return None, [Problem("structure", error)]
    return from_dict(data)


# --------------------------------------------------------------------------------------------------------------
# resolving a map against the code

def _parse_entry(raw: str, owner: str, repo: Repo, problems: list[Problem], warnings: list[str]) -> Entry | None:
    where = f"{owner_label(owner)}: '{raw}'"

    def bad(reason: str) -> None:
        problems.append(Problem("bad_entry", f"{where}: {reason}", owner=owner, entry=raw))

    s = raw.strip()
    m = _RANGE_RE.match(s)
    if m:
        path, lo, hi = m.group("path"), int(m.group("lo")), int(m.group("hi"))
        f = repo.files.get(path)
        if f is None:
            bad("no such text file")
            return None
        if not 1 <= lo <= hi <= f.n_lines:
            bad(f"lines must be within 1-{f.n_lines}")
            return None
        return Entry(raw, owner, "range", path, lo=lo, hi=hi)
    if "::" in s:
        path, piece = s.split("::", 1)
        f = repo.files.get(path)
        if f is None:
            bad("no such text file")
            return None
        if piece not in f.pieces:
            if not f.pieces:
                bad("the reader found no named pieces in this file; assign the whole file or use a line range")
            else:
                close = difflib.get_close_matches(piece, list(f.pieces), n=3)
                bad("no piece with this name in the file" + (f" (did you mean {', '.join(close)}?)" if close else ""))
            return None
        return Entry(raw, owner, "piece", path, piece=piece)
    path = s.strip("/") if s not in ("", ".", "./", "/") else ""
    if os.path.isabs(s) or path == ".." or path.startswith("../"):
        bad("paths are relative to the project folder")
        return None
    full = os.path.join(repo.root, path)
    if path == "" or s.endswith("/") or os.path.isdir(full):
        if not os.path.isdir(full):
            bad("no such folder")
            return None
        if path and not any(p.startswith(path + "/") for p in repo.files):
            warnings.append(f"{where}: the folder holds no analysed text files")
        return Entry(raw, owner, "folder", path)
    if path not in repo.files:
        if os.path.isfile(full):
            warnings.append(f"{where}: not analysed (binary, too large or ignored); the entry is not needed")
        else:
            bad("no such file")
        return None
    return Entry(raw, owner, "file", path)


def resolve(contract: Contract, repo: Repo) -> Resolution:
    problems: list[Problem] = []
    warnings: list[str] = []
    entries: list[Entry] = []
    for s in contract.subs:
        for raw in s.owns:
            e = _parse_entry(raw, s.id, repo, problems, warnings)
            if e:
                entries.append(e)
    for raw in contract.tests:
        e = _parse_entry(raw, TESTS, repo, problems, warnings)
        if e:
            entries.append(e)
    for raw, _ in contract.excluded:
        e = _parse_entry(raw, EXCLUDED, repo, problems, warnings)
        if e:
            entries.append(e)

    by_file: dict[str, list[Entry]] = {}
    folders = [e for e in entries if e.kind == "folder"]
    for e in entries:
        if e.kind != "folder":
            by_file.setdefault(e.path, []).append(e)

    owner_of: dict[str, list[str | None]] = {}
    conflicts: dict[tuple[str, str], Problem] = {}
    for path, f in repo.files.items():
        mine = [e for e in folders if e.path == "" or path.startswith(e.path + "/")] + by_file.get(path, [])
        n = f.n_lines
        owner: list[str | None] = [None] * n
        rank: list[tuple[int, int]] = [(0, 0)] * n
        source: list[Entry | None] = [None] * n
        for e in sorted(mine, key=lambda e: e.rank):        # stable: at equal rank the earlier entry wins
            if e.kind in ("folder", "file"):
                spans = [(1, n)]
            elif e.kind == "piece":
                spans = f.pieces[e.piece].spans
            else:
                spans = [(e.lo, e.hi)]
            for lo, hi in spans:
                for line in range(lo, min(hi, n) + 1):
                    i = line - 1
                    if e.rank > rank[i]:
                        owner[i], rank[i], source[i] = e.owner, e.rank, e
                    elif e.rank == rank[i] and e.owner != owner[i] and (e.owner, e.raw) not in conflicts:
                        winner = source[i]
                        conflicts[(e.owner, e.raw)] = Problem(
                            "conflict",
                            f"two owners at the same level for the same code: '{winner.raw}' -> "
                            f"{owner_label(winner.owner)} vs '{e.raw}' -> {owner_label(e.owner)}",
                            owner=e.owner, entry=e.raw, path=path)
        _fill_unnamed(path, f, owner, repo, problems)
        owner_of[path] = owner
    problems += list(conflicts.values())

    owned = Counter(o for path, f in repo.files.items() for o, nb in zip(owner_of[path], f.nonblank) if nb)
    for s in contract.subs:
        if owned.get(s.id, 0) == 0:
            problems.append(Problem("empty_owner", f"subcomponent {s.id}: owns no lines after resolution",
                                    owner=s.id))
    return Resolution(owner_of, problems, warnings)


def _fill_unnamed(path: str, f, owner: list[str | None], repo: Repo, problems: list[Problem]) -> None:
    """Lines no entry covers. A file nobody covers is a problem. In a split file: code outside any piece goes to
    the owner with the most lines in the file; a class header goes to the owner with the most lines in that class;
    a function nobody assigned is a problem."""
    n = f.n_lines
    if all(o is None for o, nb in zip(owner, f.nonblank) if nb):
        if any(f.nonblank):
            problems.append(Problem("uncovered_file",
                                    f"not covered: {path} (assign it to a subcomponent, tests or excluded)",
                                    path=path))
        return
    if all(o is not None for o in owner):
        return
    file_major = majority([o for o, nb in zip(owner, f.nonblank) if nb and o is not None])
    missing: set[str] = set()
    class_major: dict[str, str | None] = {}
    for i in range(n):
        if owner[i] is not None:
            continue
        unit = repo.units[f.line_unit[i]] if f.line_unit else None
        if unit is None or unit.kind == "file_rest":
            owner[i] = file_major
        elif unit.kind == "class_rest":
            if unit.piece not in class_major:
                lines = [ln for s, e in f.pieces[unit.piece].spans for ln in range(s, min(e, n) + 1)]
                class_major[unit.piece] = majority([owner[ln - 1] for ln in lines
                                                    if owner[ln - 1] is not None and f.nonblank[ln - 1]])
            owner[i] = class_major[unit.piece]
            if owner[i] is None and f.nonblank[i]:
                missing.add(unit.piece)
        elif f.nonblank[i]:
            missing.add(unit.piece)
    for piece in sorted(missing):
        problems.append(Problem("unassigned_piece",
                                f"not assigned: {path}::{piece} (this file is split, so every piece needs an owner)",
                                path=path, piece=piece))


def majority(owners: list[str | None]) -> str | None:
    owners = [o for o in owners if o is not None]
    if not owners:
        return None
    counts = Counter(owners)
    top = max(counts.values())
    return sorted(o for o, c in counts.items() if c == top)[0]


def check(map_path: str, repo: Repo) -> tuple[Contract | None, Resolution | None, list[Problem], list[str]]:
    """Everything wrong with the map at map_path: (contract, resolution, problems, warnings)."""
    contract, problems = load(map_path)
    if contract is None:
        return None, None, problems, []
    res = resolve(contract, repo)
    return contract, res, problems + res.problems, res.warnings
