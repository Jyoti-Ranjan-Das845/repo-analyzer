"use strict";
// Renders report.html from the data embedded in the page (REPORT_DATA = {score, map}). No network access.

(function () {
  const DATA = JSON.parse(document.getElementById("report-data").textContent);
  const S = DATA.score;
  const MAP = DATA.map;
  const RUN = S.run || {};
  const STEP_PRICE = 50;
  const PARTS = [
    ["understanding", "Reading and understanding", "read"],
    ["noise", "Noise", "noise"],
    ["finding", "Finding", "find"],
    ["checking", "Checking", "check"],
  ];
  const state = { selected: null, expanded: new Set() };

  const fmt = (n) => Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
  const fmt2 = (n) => Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 });

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  // report.html sits in <project>/.repo-analyzer/, so project files are one folder up
  const fileLink = (path, label) => h("a", { href: "../" + path.split("/").map(encodeURIComponent).join("/") }, label ?? path);

  const sortedByEffort = (items) => [...items].sort((a, b) => (b.effort || 0) - (a.effort || 0));

  function partsBar(parts, total) {
    const sum = total || PARTS.reduce((s, [k]) => s + (parts?.[k] || 0), 0) || 1;
    return h("div", { class: "bar" }, PARTS.map(([k, label]) =>
      h("i", { class: `p-${k}`, style: `width:${((parts?.[k] || 0) / sum) * 100}%`, title: `${label}: ${fmt(parts?.[k])}` })));
  }

  function legend(parts) {
    return h("div", { class: "legend" }, PARTS.map(([k, label, color]) =>
      h("span", {}, h("i", { style: `background:var(--${color})` }), `${label} ${fmt(parts?.[k])}`)));
  }

  function findOwner(id) {
    for (const c of S.components) {
      if (c.id === id) return { owner: c, comp: null };
      for (const s of c.subcomponents) if (s.id === id) return { owner: s, comp: c };
    }
    return null;
  }

  function ownsOf(id) {
    for (const c of MAP.components || []) {
      if (c.id === id) return c.subcomponents.flatMap((s) => s.owns);
      for (const s of c.subcomponents) if (s.id === id) return s.owns;
    }
    return [];
  }

  function list(items, fn, more) {
    const ul = h("ul", {}, items.map((x) => h("li", {}, fn(x))));
    return more ? [ul, h("div", {}, more)] : ul;
  }

  function entryLink(entry) {
    const path = entry.split("::")[0].split("#")[0];
    return path.endsWith("/") || path === "" ? h("code", {}, entry) : h("code", {}, fileLink(path, entry));
  }

  // ------------------------------------------------------------------ page parts
  function header() {
    const bits = [RUN.project || S.repo];
    if (RUN.commit) bits.push(`commit ${RUN.commit.slice(0, 10)}`);
    if (RUN.generated) bits.push(`analysed ${RUN.generated}`);
    return h("header", { class: "head" },
      h("div", {},
        h("h1", {}, h("span", { class: "mark" }), RUN.name || S.repo || "Project"),
        h("div", { class: "sub" }, bits.join(" · "))),
      h("div", { class: "sub" },
        `${fmt(RUN.files)} files (${RUN.listing === "git" ? "git" : "folder"})`,
        RUN.agent_status && RUN.agent_status !== "not run" ? ` · agent: ${RUN.agent_status}, ${RUN.sessions} session(s)` : ""));
  }

  function notices() {
    const out = [];
    if (RUN.warnings && RUN.warnings.length) {
      out.push(h("div", { class: "notice warn" }, h("strong", {}, "Warnings"), list(RUN.warnings, (w) => w)));
    }
    if (RUN.corrections && RUN.corrections.length) {
      out.push(h("div", { class: "notice" },
        h("strong", {}, "The agent's map was completed automatically"),
        list(RUN.corrections, (c) => c)));
    }
    const skipped = (RUN.skipped_large || 0) + (RUN.skipped_binary || 0);
    if (skipped) {
      out.push(h("div", { class: "notice" },
        `${fmt(skipped)} binary or very large file(s) were not analysed.`));
    }
    return out.length ? h("section", { class: "notices" }, out) : null;
  }

  function summary() {
    const repo = S.repository || {};
    const m = repo.metrics || {};
    return h("section", { class: "summary" },
      h("div", { class: "box stat" }, h("b", {}, fmt(repo.effort)), h("span", {}, "Effort for the whole project, in lines of work")),
      h("div", { class: "box stat" }, h("b", {}, fmt(repo.per_100_lines)), h("span", {}, "Effort per 100 own lines")),
      h("div", { class: "box stat" }, h("b", {}, fmt(m.L)), h("span", {}, `Own lines in ${S.components.length} components`)),
      h("div", { class: "box parts-box" }, h("span", { class: "label" }, "Where the effort goes"), partsBar(repo.parts), legend(repo.parts)));
  }

  function ownerRow(o, isSub, open) {
    const m = o.metrics || {};
    return h("div", {
      class: "row" + (isSub ? " sub" : "") + (state.selected === o.id ? " selected" : ""),
      onclick: () => {
        if (!isSub) {
          if (state.expanded.has(o.id) && state.selected === o.id) state.expanded.delete(o.id);
          else state.expanded.add(o.id);
        }
        state.selected = o.id;
        render();
      },
    },
    h("div", { class: "nm", title: isSub ? o.name : `${o.name} (${o.kind})` },
      isSub ? null : h("span", { class: "caret" }, open ? "▾" : "▸"),
      isSub ? null : h("span", { class: `kdot ${o.kind}` }),
      h("strong", {}, o.name)),
    h("span", { class: "n lines num" }, fmt(m.L)),
    h("span", { class: "n num" }, fmt(o.effort)),
    h("span", { class: "n num" }, fmt(o.per_100_lines)),
    h("span", { class: "barcell" }, partsBar(o.parts, o.effort)));
  }

  function table() {
    const t = h("div", { class: "box table" },
      h("div", { class: "thead" }, h("span", {}, "Component"), h("span", {}, "Own lines"), h("span", {}, "Effort"),
        h("span", {}, "Per 100"), h("span", {}, "Parts")));
    for (const c of sortedByEffort(S.components)) {
      const open = state.expanded.has(c.id);
      t.append(ownerRow(c, false, open));
      if (open) for (const s of sortedByEffort(c.subcomponents)) t.append(ownerRow(s, true, false));
    }
    t.append(h("div", { class: "kinds-legend" },
      ["feature", "foundation", "supporting"].map((k) => h("span", {}, h("i", { class: `kdot ${k}` }), k)),
      h("span", {}, "Click a component to open its subcomponents and see the evidence.")));
    return t;
  }

  function detail({ owner: o, comp }) {
    const m = o.metrics;
    if (!m) return h("div", { class: "box detail" }, h("h2", {}, o.name), h("p", { class: "muted" }, "Owns no scored lines."));
    const ev = o.evidence, cx = o.complexity, p = o.parts, C = m.C, T = m.T;
    const metric = (name, value, points, body) =>
      h("details", { class: "metric" },
        h("summary", {}, h("span", {}, name), h("span", { class: "mv" }, value), h("span", { class: "mp" }, points)),
        h("div", { class: "evidence" }, body));
    const group = (cls, title, ...items) => h("div", { class: `group ${cls}` }, h("div", { class: "group-head label" }, title), items);
    const units = (rows, total) => rows.length
      ? list(rows, (u) => [h("code", {}, u.name), " ", fileLink(u.file, u.file.split("/").pop()), ` · ${fmt(u.lines)} lines`],
        total > rows.length ? `and ${total - rows.length} more` : null)
      : "None.";
    const steps = (n) => `+${fmt(n * STEP_PRICE)}`;
    return h("div", { class: "box detail" },
      h("div", {},
        comp ? h("div", { class: "label" }, comp.name) : h("span", { class: "kind" }, o.kind),
        h("h2", {}, o.name),
        h("p", { class: "desc" }, o.description || "")),
      h("div", { class: "figures" },
        h("div", {}, h("b", {}, fmt(o.effort)), h("span", {}, "effort")),
        h("div", {}, h("b", {}, fmt(o.per_100_lines)), h("span", {}, "per 100 own lines")),
        h("div", {}, h("b", {}, fmt(m.L)), h("span", {}, "own lines"))),
      h("div", { class: "parts" },
        h("div", { class: "part" }, h("span", {}, "Reading and understanding"), h("b", {}, fmt(p.understanding)),
          h("span", { class: "mono" }, `(own ${fmt(m.L)} + outside ${fmt(m.X)}) × complexity ${fmt2(C)}`)),
        h("div", { class: "part" }, h("span", {}, "Noise"), h("b", {}, fmt(p.noise)),
          h("span", { class: "mono" }, `${fmt(m.N)} noise lines`)),
        h("div", { class: "part" }, h("span", {}, "Finding"), h("b", {}, fmt(p.finding)),
          h("span", { class: "mono" }, `50 × (files ${m.F} + hops ${m.H} + clashes ${m.K} + effects ${m.E} + hidden ${m.D})`)),
        h("div", { class: "part" }, h("span", {}, "Checking"), h("b", {}, fmt(p.checking)),
          h("span", { class: "mono" }, `(own ${fmt(m.L)} + callers ${fmt(m.R)}) × (1 − tests ${fmt2(T)})`)),
        partsBar(p, o.effort)),
      group("g-read", "Reading",
        metric("Own lines", fmt(m.L), `+${fmt(m.L * C + m.L * (1 - T))}`,
          [h("div", {}, "Owned code, as written in the component map:"), h("ul", { class: "owns" }, ownsOf(o.id).map((e) => h("li", {}, entryLink(e))))]),
        metric("Outside lines", fmt(m.X), `+${fmt(m.X * C)}`, units(ev.outside, ev.outside_count)),
        metric("Noise lines", fmt(m.N), `+${fmt(m.N)}`,
          ev.noise_files.length ? list(ev.noise_files, (n) => [fileLink(n.file), ` · ${n.gaps} gaps around ${fmt(n.own_lines)} own lines, ${fmt(n.noise)} counted`]) : "No other code between its pieces.")),
      group("g-find", "Finding",
        metric("Files", m.F, steps(m.F), list(ev.files.slice(0, 40), (f) => fileLink(f), ev.files.length > 40 ? `and ${ev.files.length - 40} more` : null)),
        metric("Hops", m.H, steps(m.H), [h("div", {}, `Starts with ${ev.hops.start.length} files.`),
          ev.hops.rounds.map((r, i) => h("div", {}, `Round ${i + 1} reveals ${r.length}: `, r.slice(0, 8).map((f, k) => [k ? ", " : "", fileLink(f, f.split("/").pop())]), r.length > 8 ? " …" : ""))]),
        metric("Name clashes", m.K, steps(m.K), ev.clashes.length ? list(ev.clashes, (c) => [h("code", {}, c.name), ` is defined ${c.definitions} times`]) : "None."),
        metric("Outward effects", m.E, steps(m.E), ev.effects.length ? list(ev.effects, (e) => h("code", {}, e), m.E > ev.effects.length ? `and ${m.E - ev.effects.length} more` : null) : "None."),
        metric("Hidden flow", m.D, steps(m.D), ev.hidden.length ? list(ev.hidden, (x) => [x.what, " · ", fileLink(x.where.split(":")[0], x.where.split("/").pop())], m.D > ev.hidden.length ? `and ${m.D - ev.hidden.length} more` : null) : "None.")),
      group("g-think", "Understanding",
        metric("Complexity", fmt2(C), `×${fmt2(C)}`, [
          h("div", {}, `${fmt(cx.code_lines)} code lines at ×${fmt2(cx.code_factor)}, ${fmt(cx.other_lines)} other lines at ×1.`),
          list(Object.entries(cx.ratios), ([k, r]) => `${k}: ${fmt2(cx.per_100[k])} per 100 lines, ×${fmt2(r)} typical`)])),
      group("g-check", "Checking",
        metric("Caller lines", fmt(m.R), `+${fmt(m.R * (1 - T))}`, units(ev.callers, ev.callers_count)),
        metric("Test protection", fmt2(T), `−${fmt((m.L + m.R) * T)}`, `${fmt(ev.tested_lines)} of ${fmt(m.L)} own lines are reached from tests.`)));
  }

  function howToRead() {
    return h("section", { class: "box how" },
      h("h3", {}, "How to read this"),
      h("div", {}, "Effort is counted in lines of work: one unit is the work of reading one line of typical code. It has no upper limit; larger and harder parts score higher. Effort per 100 own lines compares parts of different sizes."),
      h("div", {}, "Effort = (own lines + outside lines) × complexity + noise lines + 50 × (files + hops + name clashes + outward effects + hidden flow) + (own lines + caller lines) × (1 − test protection)."),
      h("div", { class: "muted" }, `Typical complexity values come from ${S.typical_source}. Files: score.json (every number), report.md, map.final.json (the scored map).`));
  }

  function render() {
    const root = document.getElementById("app");
    const sel = state.selected && findOwner(state.selected);
    root.replaceChildren(...[                       // a part with nothing to show is left out, not printed as "null"
      header(),
      notices(),
      summary(),
      h("section", { class: "results" }, table(), sel ? detail(sel) : h("div")),
      howToRead()].filter(Boolean));
  }

  const first = sortedByEffort(S.components)[0];
  if (first) state.selected = first.id;
  render();
})();
