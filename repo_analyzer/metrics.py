"""The eleven metrics and the effort for every subcomponent, component and the whole repository.

Effort = (L + X) x C  +  N  +  TRIP_COST x (F + H + K + E + D)  +  (L + R) x (1 - T)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import config as K
from .contract import EXCLUDED, TESTS, Contract, Resolution
from .imports import Imports, module_name, read_imports
from .reader import Repo

COUNT_KEYS = ("decisions", "nesting", "datadeps", "vocabulary", "concurrency")
_PATH_RE = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::|->)[A-Za-z_$][\w$]*)*")
_UNKNOWN = object()                 # no import line explains a call: the other linking rules decide


def _distinctive(name: str) -> bool:
    core = name.strip("_")
    return "_" in core or any(c.isupper() for c in core[1:]) or len(core) >= K.DISTINCT_NAME_MIN_LEN


def _majority(values) -> str | None:
    counts = Counter(values)
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]


class Scorer:
    def __init__(self, repo: Repo, contract: Contract, res: Resolution):
        self.repo, self.contract, self.res = repo, contract, res
        self.units = repo.units
        self.sub_comp = {s.id: s.component for s in contract.subs}
        # owner of each unit (subcomponent level) = owner of most of its non-blank lines
        self.unit_owner: dict[int, str | None] = {}
        for u in self.units:
            own = res.owner[u.file]
            self.unit_owner[u.id] = _majority(own[ln - 1] for ln in u.lines) if u.lines else (
                own[0] if own else None)
        self.scored = lambda o: o is not None and o not in (TESTS, EXCLUDED)

        # definitions that calls can land on: functions, and classes (a call to a class builds one)
        # keyed by (language, name): a call only lands on code written in the caller's language
        self.lang = {p: f.language for p, f in repo.files.items()}
        self.defs: dict[tuple[str | None, str], list[int]] = defaultdict(list)
        self.class_rest: dict[tuple[str, str], int] = {}
        for u in self.units:
            if u.kind == "class_rest":
                self.class_rest[(u.file, u.piece)] = u.id
            if u.kind in ("function", "class_rest") and self.scored(self.unit_owner[u.id]):
                self.defs[(self.lang[u.file], u.simple)].append(u.id)
        self.function_names = {(self.lang[u.file], u.simple) for u in self.units if u.kind == "function"
                               and self.scored(self.unit_owner[u.id])}
        self.class_names = {(self.lang[u.file], u.simple) for u in self.units if u.kind == "class_rest"}
        self.file_rest_units = {u.file: u.id for u in self.units if u.kind == "file_rest"}
        # top-level functions and classes by name: what a search for a plain call's name turns up
        self.top_defs: dict[tuple[str | None, str], list[int]] = defaultdict(list)
        for key, ids in self.defs.items():
            for c in ids:
                if "." not in (self.units[c].piece or ""):
                    self.top_defs[key].append(c)
        self.file_names: dict[str, set[str]] = defaultdict(set)      # every name a file refers to
        for u in self.units:
            self.file_names[u.file] |= u.vocab
        # where import lines point, and the top-level definitions of each file, for calls through imported names
        self.imports: Imports = read_imports(repo)
        self.top_in_file: dict[tuple[str, str], list[int]] = defaultdict(list)
        for ids in self.top_defs.values():
            for c in ids:
                self.top_in_file[(self.units[c].file, self.units[c].simple)].append(c)
        self._levels_cache: dict[frozenset, list[set[str]]] = {}
        self._hierarchy()

        # call edges: (caller unit, target unit, line owner, file, line)
        self.edges: list[tuple[int, int, str | None, str, int]] = []
        for path, f in repo.files.items():
            for call in f.calls:
                if call.runtime or not call.name:
                    continue
                target = self._resolve(call.name, path, call.unit, call.receiver)
                if target is not None:
                    self.edges.append((call.unit, target, res.owner[path][call.line - 1], path, call.line))
        self.out_edges: dict[int, set[int]] = defaultdict(set)
        self.in_edges: dict[int, list[tuple[int, str | None]]] = defaultdict(list)
        for cu, tu, lo, _, _ in self.edges:
            self.out_edges[cu].add(tu)
            self.in_edges[tu].append((cu, lo))
        self.tested_units = self._test_reach()
        self.typical = self._typical()

    # ---------------------------------------------------------------------------------------------------------
    def qualified(self, path: str, receiver: str | None) -> bool:
        """A call through self/this or through a class name says exactly where to look: no search needed."""
        if not receiver:
            return False
        return receiver in K.SELF_NAMES or (self.lang[path], receiver) in self.class_names

    def _resolve(self, name: str, path: str, caller: int, receiver: str | None = None) -> int | None:
        """Fixed rule: through a name an import line brought in, the definition in the file that line points to;
        through self/this, the method of the caller's class; through a class name, that class's method; else the
        only definition with that name; else the one in the same file (same class first); else the only one in a
        file the caller imports; else unlinked."""
        via = self._through_import(name, path, caller, receiver)
        if via is not _UNKNOWN:
            return via
        cands = self.defs.get((self.lang[path], name))
        if not cands:
            return None
        if receiver is None:
            if name in self.units[caller].assigns:
                return None                           # a local variable or a function defined in the caller
            # a plain call reaches a top-level function, a class, or a method of the caller's own class
            cands = self._plain_targets(cands, caller)
            if not cands:
                return None
        if receiver in K.SELF_NAMES:
            cls = (self.units[caller].piece or "").rsplit(".", 1)[0]
            mine = [c for c in cands if self.units[c].file == path
                    and (self.units[c].piece or "").rsplit(".", 1)[0] == cls]
            if len(mine) == 1:
                return mine[0]
        elif receiver and (self.lang[path], receiver) in self.class_names:
            mine = [c for c in cands if (self.units[c].piece or "").startswith(receiver + ".")
                    or (self.units[c].piece or "") == receiver]
            if len(mine) == 1:
                return mine[0]
        unknown_receiver = receiver is not None and receiver not in K.SELF_NAMES \
            and (self.lang[path], receiver) not in self.class_names
        if unknown_receiver:
            mentioned = self._mentioned_methods(cands, path, caller)
            if len(mentioned) == 1:
                return mentioned[0]
            if not _distinctive(name):
                return None
        if len(cands) == 1:
            return cands[0]
        same = [c for c in cands if self.units[c].file == path]
        if len(same) == 1:
            return same[0]
        if len(same) > 1:
            cls = (self.units[caller].piece or "").rsplit(".", 1)[0]
            in_class = [c for c in same if (self.units[c].piece or "").rsplit(".", 1)[0] == cls]
            return in_class[0] if len(in_class) == 1 else None
        imported_files = self.imports.files.get(path, set())
        imported = [c for c in cands if self.units[c].file in imported_files]
        if len({self.units[c].file for c in imported}) > 1:
            return None
        return imported[0] if len(imported) == 1 else None

    def _through_import(self, name: str, path: str, caller: int, receiver: str | None):
        """A call through a name an import line brought in (`bodyParser.json()` after
        `var bodyParser = require('..')`, `helper()` after `from .utils import helper`) is looked up in the project
        files that line points to, then in the files those import, up to REEXPORT_DEPTH steps on, nearest first.
        Returns the definition; None when the line points outside the project or several definitions match;
        _UNKNOWN when no import line explains the call."""
        names = self.imports.names.get(path)
        if not names:
            return _UNKNOWN
        if receiver is None:
            head, rest = name, []
        elif _PATH_RE.fullmatch(receiver):
            head, *rest = re.split(r"\.|::|->", receiver)
        else:
            return _UNKNOWN
        u = self.units[caller]
        if head not in names or (u.kind == "function" and head in u.assigns):
            return _UNKNOWN                    # not brought in by an import, or a local name that hides it
        files = names[head]
        if not files:
            return None                        # a standard or installed library: never project code
        real = self.imports.aliases.get(path, {}).get(head, head)      # `from x import a as b`: b is a

        def found_in(f: str) -> list[int]:
            if receiver is None:
                return self.top_in_file.get((f, real), [])
            hits = []
            if not rest or module_name(f) == rest[-1]:      # mod.name(), pkg.mod.name()
                hits += self.top_in_file.get((f, name), [])
            m = self.methods_of.get((f, rest[-1] if rest else real), {}).get(name)    # Class.name()
            if m is not None and self.scored(self.unit_owner[m]):
                hits.append(m)
            return hits

        for level in self._levels(files):
            hits = sorted({h for f in level for h in found_in(f)})
            if hits:
                return hits[0] if len(hits) == 1 else None
        return _UNKNOWN

    def _levels(self, files: set[str]) -> list[set[str]]:
        """The files an import points to, then the files they import, and so on, REEXPORT_DEPTH steps on."""
        key = frozenset(files)
        levels = self._levels_cache.get(key)
        if levels is None:
            levels, seen, frontier = [], set(key), set(key)
            for _ in range(K.REEXPORT_DEPTH + 1):
                levels.append(frontier)
                frontier = {g for f in frontier for g in self.imports.files.get(f, ())} - seen
                seen |= frontier
                if not frontier:
                    break
            self._levels_cache[key] = levels
        return levels

    def _import_closure(self, start) -> set[str]:
        """Every file loaded by importing these files' imports, directly or through other imports."""
        seen: set[str] = set()
        stack = [g for f in start for g in self.imports.files.get(f, ())]
        while stack:
            f = stack.pop()
            if f not in seen:
                seen.add(f)
                stack.extend(self.imports.files.get(f, ()))
        return seen

    def _mentioned_methods(self, cands: list[int], path: str, caller: int) -> list[int]:
        """Methods among `cands` whose class the calling code refers to by name. Test code is read file-wide,
        because tests usually build their objects in fixtures or helpers elsewhere in the same file."""
        if self.res.owner[path] and self._is_test_file(path):
            names = self.file_names[path]
        else:
            names = self.units[caller].vocab
        return [c for c in cands if self.units[c].kind == "function"
                and self._class_of(c).rsplit(".", 1)[-1] in names and self._class_of(c)]

    def _is_test_file(self, path: str) -> bool:
        return any(o == TESTS for o in self.res.owner[path])

    def _class_of(self, uid: int) -> str:
        u = self.units[uid]
        piece = u.piece or ""
        if u.kind == "class_rest":
            return piece
        return piece.rsplit(".", 1)[0] if "." in piece else ""

    def _plain_targets(self, cands: list[int], caller: int) -> list[int]:
        mine = self._class_of(caller)
        out = []
        for c in cands:
            u = self.units[c]
            top_level = "." not in (u.piece or "")
            if top_level or (mine and self._class_of(c) == mine and u.kind == "function"):
                out.append(c)
        return out

    def _hierarchy(self) -> None:
        """Class-hierarchy index for dispatch through self: bases and subclasses by class name (same language),
        and the methods each class defines."""
        self.class_pieces: dict[tuple[str | None, str], list[tuple[str, str]]] = defaultdict(list)
        self.class_bases: dict[tuple[str, str], list[str]] = {}
        self.children: dict[tuple[str | None, str], list[tuple[str, str]]] = defaultdict(list)
        self.methods_of: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
        for path, f in self.repo.files.items():
            lang = self.lang[path]
            for p in f.pieces.values():
                if p.kind == "class":
                    key = (path, p.name)
                    self.class_pieces[(lang, p.simple)].append(key)
                    self.class_bases[key] = p.bases
                    for b in p.bases:
                        self.children[(lang, b)].append(key)
        for u in self.units:
            if u.kind == "function" and "." in (u.piece or ""):
                self.methods_of[(u.file, u.piece.rsplit(".", 1)[0])][u.simple] = u.id

    def _overrides(self, caller: int, name: str) -> list[int]:
        """Where `self.name()` called in class A may land at run time: `name` in any subclass of A, or in a mixin
        that a subclass of A also inherits from (class-hierarchy analysis)."""
        u = self.units[caller]
        cls = self._class_of(caller)
        if not cls:
            return []
        lang = self.lang[u.file]
        out, seen, queue = [], {(u.file, cls)}, [(u.file, cls)]
        while queue and len(seen) < 200:
            key = queue.pop()
            for child in self.children.get((lang, key[1].rsplit(".", 1)[-1]), ()):
                if child in seen:
                    continue
                seen.add(child)
                queue.append(child)
                related = [child] + [k for b in self.class_bases.get(child, ())
                                     for k in self.class_pieces.get((lang, b), ())]
                for k in related:
                    m = self.methods_of.get(k, {}).get(name)
                    if m is not None and m not in out:
                        out.append(m)
        return out

    def _test_reach(self) -> set[int]:
        """Code the tests reach, following calls from test code through any code. From test code, a call on an
        object of unknown type reaches every matching method of the classes the test file refers to; a class the
        tests refer to has its body reached; a call through self also reaches the method's overrides; importing a
        file runs its top-level code, and that of the files it imports."""
        calls_by_unit: dict[int, list] = defaultdict(list)
        for path, f in self.repo.files.items():
            for call in f.calls:
                if not call.runtime and call.name:
                    calls_by_unit[call.unit].append((path, call))
        reached: set[int] = set()
        stack: list[int] = []

        def push(uid: int) -> None:
            if uid in reached:
                return
            reached.add(uid)
            stack.append(uid)
            u = self.units[uid]
            if u.kind == "class_rest":                 # building an object runs its constructor
                for name, mid in self.methods_of.get((u.file, u.piece), {}).items():
                    if name in K.CONSTRUCTOR_NAMES or name == u.simple:
                        push(mid)

        test_files = []
        for path, f in self.repo.files.items():
            owners = self.res.owner[path]
            if not any(o == TESTS for o in owners):
                continue
            test_files.append(path)
            lang = self.lang[path]
            for call in f.calls:
                if call.runtime or not call.name or owners[call.line - 1] != TESTS:
                    continue
                target = self._resolve(call.name, path, call.unit, call.receiver)
                if target is not None:
                    push(target)
                cands = self.defs.get((lang, call.name))
                if cands and call.receiver and call.receiver not in K.SELF_NAMES:
                    for m in self._mentioned_methods(cands, path, call.unit):
                        push(m)
            for name in self.file_names[path]:
                for key in self.class_pieces.get((lang, name), ()):
                    cid = self.class_rest.get(key)
                    if cid is not None and self.scored(self.unit_owner[cid]):
                        push(cid)
        for other in self._import_closure(test_files):
            rest = self.file_rest_units.get(other)
            if rest is not None and self.scored(self.unit_owner[rest]):
                push(rest)                             # importing a module runs its top-level code
        while stack:
            uid = stack.pop()
            for path, call in calls_by_unit.get(uid, ()):
                target = self._resolve(call.name, path, uid, call.receiver)
                if target is not None:
                    push(target)
                if call.receiver in K.SELF_NAMES:
                    for m in self._overrides(uid, call.name):
                        push(m)
        # a class body counts as reached when a method runs; top-level code when its file is used
        extra = set()
        files_used = {self.units[u].file for u in reached}
        for u in self.units:
            if u.kind == "file_rest" and u.file in files_used:
                extra.add(u.id)
        for u in list(reached):
            parts = (self.units[u].piece or "").split(".")
            for i in range(1, len(parts)):
                cid = self.class_rest.get((self.units[u].file, ".".join(parts[:i])))
                if cid is not None:
                    extra.add(cid)
        return reached | extra

    def _typical(self) -> dict[str, float]:
        if K.TYPICAL_PER_100:
            return dict(K.TYPICAL_PER_100)
        totals = Counter()
        lines = 0
        for path, f in self.repo.files.items():
            if not f.analyzed:
                continue
            own = self.res.owner[path]
            lines += sum(1 for o, nb in zip(own, f.nonblank) if nb and self.scored(o))
            for key, events in (("decisions", f.decisions), ("nesting", f.nesting), ("datadeps", f.datadeps),
                                ("concurrency", f.concurrency)):
                totals[key] += sum(v for ln, _, v in events if self.scored(own[ln - 1]))
        for u in self.units:
            if self.scored(self.unit_owner[u.id]) and self.repo.files[u.file].analyzed:
                totals["vocabulary"] += len(u.vocab)
        return {k: (totals[k] / lines * 100 if lines else 0.0) for k in COUNT_KEYS}

    # ---------------------------------------------------------------------------------------------------------
    def score_level(self, level: str) -> dict[str, dict]:
        """level: subcomponent | component | repo"""
        def label(owner):
            if not self.scored(owner):
                return None
            if level == "subcomponent":
                return owner
            if level == "component":
                return self.sub_comp[owner]
            return "repo"

        units, files = self.units, self.repo.files
        ulabel = {u.id: label(self.unit_owner[u.id]) for u in units}
        acc = defaultdict(lambda: {
            "L": 0, "files": set(), "events": Counter(), "writes": set(), "hidden": [], "names": set(),
            "x_units": set(), "r_units": set(), "tested": 0, "per_file": Counter(), "hidden_keys": set(),
            "code_lines": 0, "own_lines": defaultdict(list)})
        unit_events: dict[int, Counter] = defaultdict(Counter)

        for path, f in files.items():
            labels = [label(o) for o in self.res.owner[path]]
            for i, nb in enumerate(f.nonblank):
                lab = labels[i]
                if nb and lab:
                    a = acc[lab]
                    a["L"] += 1
                    a["files"].add(path)
                    a["per_file"][path] += 1
                    a["own_lines"][path].append(i)
                    if f.analyzed:
                        a["code_lines"] += 1
                    if f.line_unit and f.line_unit[i] in self.tested_units:
                        a["tested"] += 1
            for key, events in (("decisions", f.decisions), ("nesting", f.nesting), ("datadeps", f.datadeps),
                                ("concurrency", f.concurrency)):
                for ln, uid, v in events:
                    unit_events[uid][key] += v
                    lab = labels[ln - 1]
                    if lab:
                        acc[lab]["events"][key] += v
            for ln, uid, tgt in f.writes:
                if labels[ln - 1]:
                    acc[labels[ln - 1]]["writes"].add(tgt)
            for ln, uid, what in f.hidden:
                lab = labels[ln - 1]
                if not lab:
                    continue
                if what.startswith("decorator"):
                    key = (uid, "decorator")              # stacked decorators wrap one definition once
                elif what.startswith(("calls a function passed in", "call picked at run time")):
                    key = (uid, what)
                else:
                    key = (uid, what, ln)
                if key not in acc[lab]["hidden_keys"]:
                    acc[lab]["hidden_keys"].add(key)
                    acc[lab]["hidden"].append((f"{path}:{ln}", what))
            for ln, uid, name in f.fn_args:
                lab = labels[ln - 1]
                if lab and (f.language, name) in self.function_names and name not in units[uid].assigns \
                        and (uid, "fn", name) not in acc[lab]["hidden_keys"]:
                    acc[lab]["hidden_keys"].add((uid, "fn", name))
                    acc[lab]["hidden"].append((f"{path}:{ln}", f"function passed as a value: {name}"))
            for call in f.calls:
                lab = labels[call.line - 1]
                # a name is searched only when the call gives no other clue: a plain call like foo(). A call
                # through an object or module (x.get(), utils.foo()) is found through that object instead, and
                # a name an import line brings in (from .utils import helper) through that line.
                if lab and call.name and call.plain and (f.language, call.name) in self.top_defs \
                        and call.name not in units[call.unit].assigns \
                        and call.name not in self.imports.words.get(path, ()):
                    acc[lab]["names"].add((f.language, call.name))
        for u in units:
            unit_events[u.id]["vocabulary"] = len(u.vocab)
            if ulabel[u.id]:
                acc[ulabel[u.id]]["events"]["vocabulary"] += len(u.vocab)
        for cu, tu, lo, path, ln in self.edges:
            caller_lab, target_lab = label(lo), ulabel[tu]
            if caller_lab and target_lab and caller_lab != target_lab:
                acc[caller_lab]["x_units"].add(tu)
                acc[target_lab]["r_units"].add(cu)

        out = {}
        for lab, a in acc.items():
            out[lab] = self._finish(lab, a, ulabel, unit_events)
        return out

    def _finish(self, lab: str, a: dict, ulabel: dict, unit_events: dict) -> dict:
        units, files = self.units, self.repo.files
        L = a["L"]
        x_units, r_units = a["x_units"], a["r_units"]
        X = sum(len(units[u].lines) for u in x_units)
        R = sum(len(units[u].lines) for u in r_units)
        own_files = a["files"]
        noise_rows = []
        for p in sorted(own_files):
            nb = files[p].nonblank
            own = a["own_lines"][p]
            mine = set(own)
            gaps, run = [], 0
            for i in range(own[0], own[-1] + 1):
                if i in mine:
                    if run:
                        gaps.append(run)
                    run = 0
                elif nb[i]:
                    run += 1
            noise_rows.append((p, files[p].code_lines, len(own), sum(min(g, K.TRIP_COST) for g in gaps), len(gaps)))
        N = sum(r[3] for r in noise_rows)
        all_files = own_files | {units[u].file for u in x_units} | {units[u].file for u in r_units}
        F = len(all_files)
        H, rounds, start_files = self._hops(lab, ulabel, own_files)
        clashes = sorted(((n[1], len(self.top_defs[n])) for n in a["names"] if len(self.top_defs[n]) > 1),
                         key=lambda t: (-t[1], t[0]))
        Kc = sum(c - 1 for _, c in clashes)
        E = len(a["writes"])
        D = len(a["hidden"])
        T = a["tested"] / L if L else 0.0

        totals = Counter(a["events"])
        for u in x_units:
            totals.update(unit_events[u])
        code = a["code_lines"] + X                  # outside code is always code: it is a called function
        other = L - a["code_lines"]
        clines = L + X
        per100 = {k: (totals[k] / code * 100 if code else 0.0) for k in COUNT_KEYS}
        ratios = {k: (per100[k] / self.typical[k] if self.typical[k] else 0.0) for k in COUNT_KEYS}
        c_code = (ratios["decisions"] + ratios["nesting"] + ratios["datadeps"] + ratios["vocabulary"]) / 4 \
            + ratios["concurrency"] / 5
        C = (c_code * code + 1.0 * other) / clines if clines else 0.0

        parts = {
            "understanding": (L + X) * C,
            "noise": float(N),
            "finding": K.TRIP_COST * (F + H + Kc + E + D),
            "checking": (L + R) * (1 - T),
        }
        effort = sum(parts.values())

        def top_units(ids, n=8):
            rows = sorted(((units[u].name, units[u].file, len(units[u].lines)) for u in ids),
                          key=lambda r: (-r[2], r[1], r[0]))
            return [{"name": nm, "file": fl, "lines": ln} for nm, fl, ln in rows[:n]]

        return {
            "metrics": {"L": L, "X": X, "N": N, "F": F, "H": H, "K": Kc, "E": E, "D": D,
                        "C": round(C, 3), "R": R, "T": round(T, 3)},
            "parts": {k: round(v, 1) for k, v in parts.items()},
            "effort": round(effort, 1),
            "per_100_lines": round(effort / L * 100, 1) if L else 0.0,
            "complexity": {"lines": clines, "code_lines": code, "other_lines": other, "code_factor": round(c_code, 3),
                           "totals": dict(totals), "per_100": {k: round(v, 2) for k, v in per100.items()},
                           "ratios": {k: round(v, 3) for k, v in ratios.items()}},
            "evidence": {
                "outside": top_units(x_units), "outside_count": len(x_units),
                "callers": top_units(r_units), "callers_count": len(r_units),
                "noise_files": [{"file": p, "file_lines": t, "own_lines": m, "noise": n, "gaps": g}
                                for p, t, m, n, g in sorted(noise_rows, key=lambda r: -r[3]) if n][:8],
                "files": sorted(all_files),
                "hops": {"start": sorted(start_files), "rounds": rounds},
                "clashes": [{"name": n, "definitions": c} for n, c in clashes[:12]],
                "effects": sorted(a["writes"])[:20],
                "hidden": [{"where": w, "what": h} for w, h in sorted(a["hidden"])[:20]],
                "tested_lines": a["tested"],
            },
        }

    def _hops(self, lab: str, ulabel: dict, own_files: set[str]):
        units = self.units
        own = [u for u in units if ulabel[u.id] == lab]
        entries = []
        for u in own:
            if u.kind != "function":
                continue
            callers = [ulabel[cu] for cu, lo in self.in_edges.get(u.id, []) if lo not in (TESTS, EXCLUDED)]
            if not callers or any(c != lab for c in callers):
                entries.append(u)
        start = {u.file for u in entries} or set(own_files)
        own_by_file = defaultdict(list)
        for u in own:
            own_by_file[u.file].append(u)

        def run(opened: set[str]):
            opened = set(opened)
            rounds = []
            while True:
                pending = set()
                for p in opened:
                    for q in self.imports.files.get(p, ()):
                        if q in own_files and q not in opened:
                            pending.add(q)
                    for u in own_by_file.get(p, ()):
                        for t in self.out_edges.get(u.id, ()):
                            tf = units[t].file
                            if tf not in opened and ulabel[t] is not None:
                                pending.add(tf)
                if not pending:
                    return opened, rounds
                rounds.append(sorted(pending))
                opened |= pending

        opened, rounds = run(start)
        leftover = own_files - opened
        if leftover:                      # own files nothing leads to are opened at the start
            start |= leftover
            opened, rounds = run(start)
        return len(rounds), rounds, start


def score(repo: Repo, contract: Contract, res: Resolution) -> dict:
    s = Scorer(repo, contract, res)
    levels = {lvl: s.score_level(lvl) for lvl in ("subcomponent", "component", "repo")}
    comps = []
    for c in contract.components:
        row = {"id": c.id, "name": c.name, "kind": c.kind, "description": c.description,
               **levels["component"].get(c.id, {}), "subcomponents": []}
        for sub in c.subs:
            row["subcomponents"].append({"id": sub.id, "name": sub.name, "description": sub.description,
                                         **levels["subcomponent"].get(sub.id, {})})
        comps.append(row)
    return {
        "repo": contract.repo,
        "commit": contract.commit,
        "formula": "Effort = (L + X) x C + N + %d x (F + H + K + E + D) + (L + R) x (1 - T)" % K.TRIP_COST,
        "typical_per_100_lines": {k: round(v, 2) for k, v in s.typical.items()},
        "typical_source": "fixed benchmark" if K.TYPICAL_PER_100 else "this repository's own average",
        "call_links": len(s.edges),
        "repository": levels["repo"].get("repo", {}),
        "components": comps,
    }
