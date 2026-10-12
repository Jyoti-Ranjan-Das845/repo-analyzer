"""A readable report of the scores, with the evidence behind every part so each number can be checked."""

from __future__ import annotations

KEYS = ("L", "X", "N", "F", "H", "K", "E", "D", "C", "R", "T")


def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}" if v < 10 else f"{v:,.0f}"
    return f"{v:,}"


def _row(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _table(rows: list[dict], first: list[str]) -> list[str]:
    head = first + list(KEYS) + ["Effort", "Per 100 lines"]
    out = [_row(head), _row(["---"] * len(first) + ["---:"] * (len(head) - len(first)))]
    for r in rows:
        m = r.get("metrics", {})
        out.append(_row([r[k] for k in r["_first"]] + [_num(m.get(k, 0)) for k in KEYS]
                        + [_num(r.get("effort", 0)), _num(r.get("per_100_lines", 0))]))
    return out


def _details(title: str, r: dict) -> list[str]:
    if "metrics" not in r:
        return [f"### {title}", "", "Owns no scored lines.", ""]
    m, p, ev, cx = r["metrics"], r["parts"], r["evidence"], r["complexity"]
    out = [f"### {title}", ""]
    out.append(f"Effort **{_num(r['effort'])}** = understanding {_num(p['understanding'])} + noise {_num(p['noise'])}"
               f" + finding {_num(p['finding'])} + checking {_num(p['checking'])}"
               f"  ·  per 100 lines {_num(r['per_100_lines'])}")
    out.append("")
    out.append(f"- **C {m['C']}** (code ×{cx['code_factor']} on {cx['code_lines']:,} code lines, "
               f"{cx['other_lines']:,} other lines at 1.0): " + ", ".join(
        f"{k} {cx['per_100'][k]}/100 (×{cx['ratios'][k]})" for k in cx["ratios"]))
    out.append(f"- **T {m['T']}**: {ev['tested_lines']:,} of {m['L']:,} own lines reached from tests")
    if ev["outside"]:
        out.append(f"- **X {m['X']:,}** from {ev['outside_count']} functions, largest: " + "; ".join(
            f"`{u['name']}` ({u['file']}, {u['lines']})" for u in ev["outside"][:5]))
    if ev["callers"]:
        out.append(f"- **R {m['R']:,}** from {ev['callers_count']} functions, largest: " + "; ".join(
            f"`{u['name']}` ({u['file']}, {u['lines']})" for u in ev["callers"][:5]))
    if ev["noise_files"]:
        out.append(f"- **N {m['N']:,}**: " + "; ".join(
            f"{n['file']} ({n['gaps']} gaps between {n['own_lines']} own lines of {n['file_lines']})"
            for n in ev["noise_files"][:5]))
    out.append(f"- **F {m['F']}**, **H {m['H']}**: start with {len(ev['hops']['start'])} files" + "".join(
        f"; round {i + 1} opens {len(rnd)} ({', '.join(rnd[:4])}{'…' if len(rnd) > 4 else ''})"
        for i, rnd in enumerate(ev["hops"]["rounds"])))
    if ev["clashes"]:
        out.append(f"- **K {m['K']}**: " + ", ".join(f"`{c['name']}` ×{c['definitions']}" for c in ev["clashes"][:8]))
    if ev["effects"]:
        out.append(f"- **E {m['E']}**: " + ", ".join(f"`{e}`" for e in ev["effects"][:10])
                   + (" …" if m["E"] > 10 else ""))
    if ev["hidden"]:
        out.append(f"- **D {m['D']}**: " + "; ".join(f"{h['what']} ({h['where']})" for h in ev["hidden"][:6])
                   + (" …" if m["D"] > 6 else ""))
    out.append("")
    return out


def render(result: dict) -> str:
    out = [f"# Effort report: {result['repo']}", "",
           f"Commit `{result['commit']}`. {result['formula']}.",
           f"Typical complexity values per 100 lines ({result['typical_source']}): "
           + ", ".join(f"{k} {v}" for k, v in result["typical_per_100_lines"].items())
           + f". Call links found: {result['call_links']:,}.", ""]
    repo = dict(result["repository"], name="Whole repository", _first=["name"])
    out += ["## Repository", ""] + _table([repo], ["name"]) + [""]

    comps = [dict(c, _first=["name", "kind"]) for c in result["components"]]
    comps.sort(key=lambda c: -c.get("effort", 0))
    out += ["## Components (largest effort first)", ""] + _table(comps, ["name", "kind"]) + [""]

    subs = []
    for c in result["components"]:
        for s in c["subcomponents"]:
            subs.append(dict(s, component=c["name"], _first=["name", "component"]))
    subs.sort(key=lambda s: -s.get("per_100_lines", 0))
    out += ["## Subcomponents (hardest per line first)", ""] + _table(subs, ["name", "component"]) + [""]

    out += ["## Evidence", ""]
    for c in sorted(result["components"], key=lambda c: -c.get("effort", 0)):
        out += _details(f"{c['name']} ({c['kind']})", c)
        for s in c["subcomponents"]:
            out += _details(f"{c['name']} › {s['name']}", s)
    return "\n".join(out)
