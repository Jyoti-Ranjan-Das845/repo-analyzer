"""Deterministic completion: turn whatever map the agent left into a valid one, so a run always ends with scores.

The agent is asked to make the map complete and gets several rounds to fix what the checker reports. If problems
remain (or the agent could not run at all), these fixed rules finish the job, and every change is recorded so the
report can show it:

- a map that is not valid JSON, or has the wrong shape, is rebuilt from what can be read;
- missing ids, names, descriptions and kinds get defaults; duplicate ids are renumbered;
- an entry that names no file, folder or piece is removed;
- of two entries that give the same code to different owners at the same level, the later one is removed;
- a code file nobody covers goes to the owner of the nearest owned files in the folder tree (never to
  'excluded'), else to an "Unassigned" component; any other file goes to the owner of the closest folder above it
  that has owned files, else it is excluded;
- a function nobody owns in a split file goes to the owner of most of that file;
- a subcomponent that owns nothing is removed, and so is a component left without subcomponents.
"""

from __future__ import annotations

import copy
import json
import os
from collections import Counter, defaultdict

from .contract import EXCLUDED, KINDS, TESTS, Contract, Resolution, from_dict, majority, read_json, resolve
from .reader import Repo

UNASSIGNED_ID = "unassigned"
UNASSIGNED_SUB_ID = "unassigned-s1"
MAX_ROUNDS = 6


def _skeleton(repo_name: str, commit: str) -> dict:
    return {"repo": repo_name, "commit": commit, "tests": [], "excluded": [], "components": []}


def normalize(data: object, repo_name: str, commit: str, notes: list[str]) -> dict:
    """The map's shape made valid: every field present and of the right type, ids unique."""
    if not isinstance(data, dict):
        notes.append("The map was not a JSON object; it was rebuilt from scratch")
        return _skeleton(repo_name, commit)
    out = _skeleton(repo_name, commit)
    tests = data.get("tests", [])
    out["tests"] = [t.strip() for t in tests if isinstance(t, str) and t.strip()] if isinstance(tests, list) else []
    excluded = data.get("excluded", [])
    for item in excluded if isinstance(excluded, list) else []:
        if isinstance(item, str) and item.strip():
            out["excluded"].append({"path": item.strip(), "reason": ""})
        elif isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
            out["excluded"].append({"path": item["path"].strip(), "reason": str(item.get("reason", ""))})
    used: set[str] = set()

    def unique(wanted: object, fallback: str) -> str:
        base = wanted.strip() if isinstance(wanted, str) and wanted.strip() else fallback
        new, n = base, 2
        while new in used:
            new, n = f"{base}-{n}", n + 1
        if new != base and isinstance(wanted, str) and wanted.strip():
            notes.append(f"Renamed the duplicate id '{base}' to '{new}'")
        used.add(new)
        return new

    comps = data.get("components", [])
    for ci, c in enumerate(comps if isinstance(comps, list) else [], start=1):
        if not isinstance(c, dict):
            continue
        cid = unique(c.get("id"), f"c{ci}")
        kind = c.get("kind") if c.get("kind") in KINDS else "feature"
        if c.get("kind") not in KINDS:
            notes.append(f"Component {cid}: kind '{c.get('kind')}' is not one of {', '.join(KINDS)}; set to feature")
        comp = {"id": cid, "name": _text(c.get("name"), f"Component {ci}"), "kind": kind,
                "description": _text(c.get("description"), "No description given."), "subcomponents": []}
        subs = c.get("subcomponents")
        if not isinstance(subs, list) or not subs:
            subs = [{"id": f"{cid}s1", "name": comp["name"], "description": comp["description"],
                     "owns": c.get("owns", [])}]
        for si, s in enumerate(subs, start=1):
            if not isinstance(s, dict):
                continue
            owns = s.get("owns", [])
            owns = [o.strip() for o in owns if isinstance(o, str) and o.strip()] if isinstance(owns, list) else []
            comp["subcomponents"].append({"id": unique(s.get("id"), f"{cid}s{si}"),
                                          "name": _text(s.get("name"), f"Part {si}"),
                                          "description": _text(s.get("description"), "No description given."),
                                          "owns": owns})
        out["components"].append(comp)
    return out


def _text(value: object, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _owner_lists(data: dict) -> dict[str, list]:
    """Where each owner's entries live in the map dict."""
    lists: dict[str, list] = {TESTS: data["tests"], EXCLUDED: data["excluded"]}
    for c in data["components"]:
        for s in c["subcomponents"]:
            lists[s["id"]] = s["owns"]
    return lists


def _remove_entry(data: dict, owner: str, entry: str) -> bool:
    lst = _owner_lists(data).get(owner)
    if lst is None:
        return False
    for i, item in enumerate(lst):
        raw = item["path"] if isinstance(item, dict) else item
        if raw == entry:
            del lst[i]
            return True
    return False


def _add_entry(data: dict, owner: str, entry: str, reason: str = "") -> None:
    if owner == EXCLUDED:
        data["excluded"].append({"path": entry, "reason": reason or "not placed by the map; not code"})
    elif owner == TESTS:
        data["tests"].append(entry)
    else:
        _owner_lists(data)[owner].append(entry)


def _folder_counts(res: Resolution, repo: Repo) -> dict[str, dict[bool, Counter]]:
    """For every folder: owned non-blank lines per owner of the files directly in it, split into code files
    (True) and other files (False)."""
    out: dict[str, dict[bool, Counter]] = defaultdict(lambda: {True: Counter(), False: Counter()})
    for p, owners in res.owner.items():
        f = repo.files[p]
        per_file = Counter(o for o, nb in zip(owners, f.nonblank) if nb and o is not None)
        if per_file:
            out[os.path.dirname(p)][f.analyzed].update(per_file)
    return out


def _distance(a: str, b: str) -> int:
    pa, pb = (a.split("/") if a else []), (b.split("/") if b else [])
    common = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        common += 1
    return (len(pa) - common) + (len(pb) - common)


def _nearest_owner(path: str, is_code: bool, counts: dict[str, dict[bool, Counter]]) -> str | None:
    """Who should own a file the map left out.

    A code file goes to the owner of the nearest owned files anywhere in the folder tree (most lines wins among
    equally near ones), never to 'excluded'. Any other file (docs, configuration, templates, styles) goes to the
    owner of the closest folder above it, or itself, that has owned files; with none, it is excluded."""
    folder = os.path.dirname(path)
    if not is_code:
        while True:
            by_kind = counts.get(folder)
            if by_kind:
                c = Counter(by_kind[True])
                c.update(by_kind[False])
                if c:
                    top = max(c.values())
                    return sorted(o for o, n in c.items() if n == top)[0]
            if not folder:
                return EXCLUDED
            folder = os.path.dirname(folder)
    best_d, best = None, Counter()
    for other, by_kind in counts.items():
        c = Counter(by_kind[True])
        c.update(by_kind[False])
        c.pop(EXCLUDED, None)
        if not c:
            continue
        d = _distance(folder, other)
        if best_d is None or d < best_d:
            best_d, best = d, c
        elif d == best_d:
            best.update(c)
    if not best:
        return None
    top = max(best.values())
    return sorted(o for o, n in best.items() if n == top)[0]


def _ensure_unassigned(data: dict) -> str:
    for c in data["components"]:
        if c["id"] == UNASSIGNED_ID:
            return c["subcomponents"][0]["id"]
    data["components"].append({
        "id": UNASSIGNED_ID, "name": "Unassigned", "kind": "supporting",
        "description": "Code the component map did not place, gathered here so that every file is counted.",
        "subcomponents": [{"id": UNASSIGNED_SUB_ID, "name": "Unassigned files",
                           "description": "Files the component map did not place.", "owns": []}]})
    return UNASSIGNED_SUB_ID


def complete(map_path: str, repo: Repo, repo_name: str, commit: str) -> tuple[dict, Contract, Resolution, list[str]]:
    """Read the agent's map, fix every remaining problem with the rules above, and return
    (map dict, contract, resolution, corrections). The map file itself is not changed."""
    notes: list[str] = []
    data, error = read_json(map_path)
    if error:
        notes.append(f"The agent's map could not be read ({error}); the map was rebuilt from scratch")
    original = copy.deepcopy(data)
    data = normalize(data, repo_name, commit, notes)
    counts: Counter = Counter()
    contract, res = None, None
    for _ in range(MAX_ROUNDS):
        contract, structural = from_dict(data)
        res = resolve(contract, repo)
        problems = [p for p in structural + res.problems if p.kind != "structure" or "no components" not in p.message]
        if not problems:
            break
        changed = False
        counts_by_folder = _folder_counts(res, repo)
        for p in problems:
            if p.kind in ("bad_entry", "conflict") and p.owner and p.entry is not None:
                if _remove_entry(data, p.owner, p.entry):
                    counts["removed_bad" if p.kind == "bad_entry" else "removed_conflict"] += 1
                    changed = True
            elif p.kind == "uncovered_file" and p.path:
                owner = _nearest_owner(p.path, repo.files[p.path].analyzed, counts_by_folder)
                if owner is None:
                    owner = _ensure_unassigned(data)
                    counts["to_unassigned"] += 1
                else:
                    counts["to_folder_owner"] += 1
                _add_entry(data, owner, p.path)
                changed = True
            elif p.kind == "unassigned_piece" and p.path and p.piece:
                owner = majority(res.owner.get(p.path, [])) or _ensure_unassigned(data)
                _add_entry(data, owner, f"{p.path}::{p.piece}")
                counts["piece_to_file_owner"] += 1
                changed = True
            elif p.kind == "empty_owner" and p.owner:
                for c in data["components"]:
                    before = len(c["subcomponents"])
                    c["subcomponents"] = [s for s in c["subcomponents"] if s["id"] != p.owner]
                    if len(c["subcomponents"]) != before:
                        counts["removed_empty_sub"] += 1
                        changed = True
                kept = [c for c in data["components"] if c["subcomponents"]]
                counts["removed_empty_comp"] += len(data["components"]) - len(kept)
                data["components"] = kept
        if not changed:
            break
    if contract is not None and not contract.components:
        _ensure_unassigned(data)
        contract, _ = from_dict(data)
        res = resolve(contract, repo)
    wording = {
        "removed_bad": "Removed {n} entr{ies} naming no existing file, folder or piece",
        "removed_conflict": "Removed {n} entr{ies} that gave code another owner already had at the same level",
        "to_folder_owner": "Gave {n} uncovered file{s} to the owner of the nearest files in the folder tree",
        "to_unassigned": "Put {n} uncovered file{s} with no owner nearby into 'Unassigned'",
        "piece_to_file_owner": "Gave {n} unowned function{s} or class{es} to the owner of most of their file",
        "removed_empty_sub": "Removed {n} subcomponent{s} that owned no code",
        "removed_empty_comp": "Removed {n} component{s} left without subcomponents",
    }
    for key, template in wording.items():
        n = counts[key]
        if n:
            notes.append(template.format(n=n, s="" if n == 1 else "s", ies="y" if n == 1 else "ies",
                                         es="" if n == 1 else "es"))
    if original is not None and original != data and not notes:
        notes.append("Normalized the map's format")
    return data, contract, res, notes


def write_json(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
