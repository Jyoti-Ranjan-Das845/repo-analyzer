"""Reads a repository with tree-sitter and records the facts the metrics need.

For every text file: its lines and its named pieces (functions, classes, methods). For code files also: calls,
writes to shared values, hidden-flow sites and the five complexity counts, each tied to a line and a unit.
The same rules (config.py) are used for every language; nothing here is written per language.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import tree_sitter as ts
import tree_sitter_language_pack as tslp

from . import config as K
from .files import Listing, list_project_files

READER_VERSION = 11          # bump when the facts the reader records change; old caches are then ignored
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "repo-analyzer" / "facts"


@dataclass
class Piece:
    """A named block the component map can point at: a function, method or class (`Class.method`)."""
    name: str
    kind: str                                   # function | class
    spans: list[tuple[int, int]]                # 1-based inclusive line spans (several when a name repeats)
    nodes: list = field(default_factory=list, repr=False)       # definition nodes (decorators excluded)
    bases: list[str] = field(default_factory=list)              # classes only: names in the class header

    @property
    def simple(self) -> str:
        return self.name.rsplit(".", 1)[-1]


@dataclass
class Unit:
    """The blocks metrics are counted in: a function or method, the rest of a class (header, attributes) or the
    rest of a file (imports, constants, top-level statements)."""
    id: int
    file: str
    name: str
    kind: str                                   # function | class_rest | file_rest
    piece: str | None                           # qualified piece name, None for the rest of a file
    lines: set[int] = field(default_factory=set)        # non-blank lines
    params: set[str] = field(default_factory=set)
    final_bytes: set[int] = field(default_factory=set)
    vocab: set[str] = field(default_factory=set)
    assigns: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    reads: list[tuple[str, int, int, int, int]] = field(default_factory=list)   # name, line, byte, loop span

    @property
    def simple(self) -> str:
        return (self.piece or "").rsplit(".", 1)[-1]


@dataclass
class Call:
    line: int
    unit: int
    name: str | None            # the called name (last part of a.b.c), None when it cannot be named
    runtime: bool               # target picked at run time: handlers[k](), f()()
    plain: bool                 # a bare name with no receiver (so it may be a parameter)
    receiver: str | None = None # what the method is called on: self, a class name, a variable ...


@dataclass
class FileFacts:
    path: str
    language: str | None
    analyzed: bool
    nonblank: list[bool]                         # index 0 = line 1
    pieces: dict[str, Piece] = field(default_factory=dict)
    line_unit: list[int] = field(default_factory=list)          # unit id per line, index 0 = line 1
    calls: list[Call] = field(default_factory=list)
    # complexity events: (line, unit, value)
    decisions: list[tuple[int, int, int]] = field(default_factory=list)
    nesting: list[tuple[int, int, int]] = field(default_factory=list)
    datadeps: list[tuple[int, int, int]] = field(default_factory=list)
    concurrency: list[tuple[int, int, int]] = field(default_factory=list)
    hidden: list[tuple[int, int, str]] = field(default_factory=list)     # (line, unit, what)
    writes: list[tuple[int, int, str]] = field(default_factory=list)     # (line, unit, target)
    fn_args: list[tuple[int, int, str]] = field(default_factory=list)    # names passed as arguments
    import_text: str = ""
    parse_errors: int = 0

    @property
    def n_lines(self) -> int:
        return len(self.nonblank)

    @property
    def code_lines(self) -> int:
        return sum(self.nonblank)


@dataclass
class Repo:
    root: str
    files: dict[str, FileFacts]
    units: list[Unit]
    source: str = "folder"                       # how the file list was made: "git" or "folder"
    skipped_large: list[str] = field(default_factory=list)      # text files above config.MAX_FILE_BYTES
    skipped_binary: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------------------------
# tree-sitter helpers

@lru_cache(maxsize=None)
def _parser(language: str):
    try:
        return tslp.get_parser(language)
    except Exception:
        return None


@lru_cache(maxsize=None)
def _tags_query(language: str):
    try:
        src = tslp.get_tags_query(language)
    except Exception:
        return None
    if not src:
        return None
    try:
        return ts.Query(tslp.get_language(language), src)
    except Exception:
        return None


def _is_name_leaf(node) -> bool:
    return node.child_count == 0 and (node.type.endswith("identifier") or node.type in K.NAME_LEAF_TYPES)


def _leaves(node):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.child_count == 0:
            yield n
        else:
            stack.extend(reversed(n.children))


def _first_name_leaf(node, extra=()):
    for leaf in _leaves(node):
        if leaf.is_named and (_is_name_leaf(leaf) or leaf.type in extra):
            return leaf
    return None


def _last_name_leaf(node):
    last = None
    for leaf in _leaves(node):
        if leaf.is_named and _is_name_leaf(leaf):
            last = leaf
    return last


# --------------------------------------------------------------------------------------------------------------
# files

def _read_text(full: str) -> bytes | None:
    with open(full, "rb") as fh:
        raw = fh.read()
    if b"\0" in raw[:K.BINARY_SNIFF_BYTES]:
        return None
    return raw


def _detect_language(path: str) -> str | None:
    try:
        return tslp.detect_language_from_path(path)
    except Exception:
        return None


# --------------------------------------------------------------------------------------------------------------
# pieces

def _definitions(language: str, root, text) -> list[tuple]:
    """(definition node, span node, name, class_like) from the tags query, else from the generic rule."""
    found, seen = [], set()
    query = _tags_query(language)
    if query is not None:
        for _, caps in ts.QueryCursor(query).matches(root):
            key = next((k for k in caps if k.startswith("definition.")), None)
            if key is None or "name" not in caps:
                continue
            kind = key.split(".", 1)[1]
            if kind in K.NON_PIECE_KINDS:
                continue
            node = caps[key][0]
            if (node.start_byte, node.end_byte) in seen:
                continue
            seen.add((node.start_byte, node.end_byte))
            found.append((node, text(caps["name"][0]), kind in K.CLASS_KINDS))
    else:
        stack = [root]
        while stack:
            n = stack.pop()
            if n.is_named and K.GENERIC_DEF_RE.match(n.type):
                name = n.child_by_field_name("name")
                if name is not None and (n.start_byte, n.end_byte) not in seen:
                    seen.add((n.start_byte, n.end_byte))
                    found.append((n, text(name), bool(K.GENERIC_CLASS_RE.match(n.type))))
            stack.extend(n.children)
    out = []
    for node, name, class_like in found:
        span = node
        while span.parent is not None and span.parent.type in K.WRAPPER_TYPES:
            span = span.parent
        out.append((node, span, name.strip(), class_like))
    out.sort(key=lambda d: (d[1].start_byte, -d[1].end_byte))
    return out


def _qualifier(node, text, def_nodes: set) -> str | None:
    """Owner type of a method that is written outside its class (Go receivers, Rust impl blocks)."""
    recv = node.child_by_field_name("receiver")
    if recv is not None:
        leaf = next((l for l in _leaves(recv) if l.type == "type_identifier"), None)
        if leaf is not None:
            return text(leaf)
    anc = node.parent
    while anc is not None:
        if (anc.start_byte, anc.end_byte) in def_nodes:
            return None
        if K.GENERIC_CLASS_RE.match(anc.type):
            ref = anc.child_by_field_name("name") or anc.child_by_field_name("type")
            if ref is not None and _is_name_leaf(ref):
                return text(ref)
        anc = anc.parent
    return None


_HEADER_SKIP_TYPES = {"modifiers", "decorator", "type_parameters", "annotation", "marker_annotation", "comment"}


def _class_bases(node, text) -> list[str]:
    """Names written in a class header outside its name and body: the base classes, mixins and interfaces
    (`class A(B, C)`, `class A extends B implements C`). Read from the syntax tree the same way for every
    language."""
    names = []
    for i, child in enumerate(node.children):
        if node.field_name_for_child(i) in ("name", "body") or child.type in _HEADER_SKIP_TYPES \
                or "body" in child.type:
            continue
        for leaf in _leaves(child):
            if leaf.is_named and _is_name_leaf(leaf):
                name = text(leaf)
                if name not in names:
                    names.append(name)
    return names


def _pieces(defs, text) -> dict[str, Piece]:
    pieces: dict[str, Piece] = {}
    def_nodes = {(d[1].start_byte, d[1].end_byte) for d in defs}
    stack: list[tuple] = []           # enclosing (span_end_byte, qualified name, class_like)
    for node, span, name, class_like in defs:
        while stack and span.start_byte >= stack[-1][0]:
            stack.pop()
        if any(not c for _, _, c in stack):
            continue                  # inside a function: part of that function, not a piece of its own
        if stack:
            qname = stack[-1][1] + "." + name
        else:
            q = _qualifier(node, text, def_nodes)
            qname = f"{q}.{name}" if q else name
        p = pieces.get(qname)
        line_span = (span.start_point[0] + 1, span.end_point[0] + 1)
        bases = _class_bases(node, text) if class_like else []
        if p is None:
            pieces[qname] = Piece(qname, "class" if class_like else "function", [line_span], [node], bases)
        else:                         # same name twice in one file (overloads, trait impls): one piece
            p.spans.append(line_span)
            p.nodes.append(node)
            p.bases += [b for b in bases if b not in p.bases]
            if class_like:
                p.kind = "class"
        stack.append((span.end_byte, qname, class_like))
    return pieces


# --------------------------------------------------------------------------------------------------------------
# units

def _make_units(path: str, facts: FileFacts, units: list[Unit]) -> None:
    n = facts.n_lines
    rest = Unit(len(units), path, "(top level)", "file_rest", None)
    units.append(rest)
    paint = [rest.id] * n
    by_piece: dict[str, Unit] = {}
    order = sorted(facts.pieces.values(), key=lambda p: -max(e - s for s, e in p.spans))
    for p in order:
        kind = "function" if p.kind == "function" else "class_rest"
        label = p.name if kind == "function" else f"{p.name} (class body)"
        u = Unit(len(units), path, label, kind, p.name)
        units.append(u)
        by_piece[p.name] = u
        for s, e in p.spans:
            for line in range(s, min(e, n) + 1):
                paint[line - 1] = u.id
    facts.line_unit = paint
    for i, uid in enumerate(paint):
        if facts.nonblank[i]:
            units[uid].lines.add(i + 1)


def _final_start(def_node) -> int | None:
    body = def_node.child_by_field_name("body")
    if body is None:
        return None
    last = body
    while last.named_children and K.BODY_WRAPPERS_RE.search(last.type):
        last = last.named_children[-1]
    return last.start_byte


def _param_name(node, text) -> str | None:
    if _is_name_leaf(node) or node.type in K.RECEIVER_TYPES:
        return text(node)
    for f in ("name", "pattern", "left"):
        sub = node.child_by_field_name(f)
        if sub is not None:
            return _param_name(sub, text)
    leaf = _first_name_leaf(node, extra=K.RECEIVER_TYPES)
    return text(leaf) if leaf is not None else None


def _setup_function_units(facts: FileFacts, units: list[Unit], text) -> None:
    for p in facts.pieces.values():
        if p.kind != "function":
            continue
        u = next(x for x in units if x.file == facts.path and x.piece == p.name and x.kind == "function")
        for node in p.nodes:
            start = _final_start(node)
            if start is not None:
                u.final_bytes.add(start)
            def_line = node.start_point[0] + 1
            for fname in ("parameters", "receiver"):
                plist = node.child_by_field_name(fname)
                if plist is None:
                    continue
                for c in plist.named_children:
                    name = _param_name(c, text)
                    if name:
                        u.params.add(name)
                        u.assigns.setdefault(name, []).append((def_line, c.start_byte))


# --------------------------------------------------------------------------------------------------------------
# the walk

def _analyze(facts: FileFacts, root, raw: bytes, units: list[Unit]) -> None:
    def text(node) -> str:
        return raw[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def unit_of(node) -> Unit:
        row = min(node.start_point[0], len(facts.line_unit) - 1)
        return units[facts.line_unit[row]]

    not_reads: set[int] = set()
    piece_starts = {node.start_byte for p in facts.pieces.values() for node in p.nodes}

    def target(node, u: Unit, line: int, plain_only: bool, augmented: bool) -> None:
        if node is None:
            return
        if _is_name_leaf(node):
            u.assigns.setdefault(text(node), []).append((line, node.start_byte))
            if not augmented:
                not_reads.add(node.start_byte)
            return
        if node.type in K.RECEIVER_TYPES:
            return
        if (K.PATTERN_RE.search(node.type) or "declarator" in node.type) and not (
                K.MEMBER_RE.search(node.type) or node.type in K.INDEX_TYPES):
            for c in node.named_children:
                target(c, u, line, plain_only, augmented)
            return
        if plain_only:
            return
        base = _first_name_leaf(node, extra=K.RECEIVER_TYPES)
        if base is not None:
            not_reads.add(base.start_byte)
        tgt = " ".join(text(node).split())[:80]
        receiver = base is not None and (base.type in K.RECEIVER_TYPES or text(base) in K.SELF_NAMES)
        if receiver and u.piece and "." in u.piece:
            tgt = u.piece.rsplit(".", 1)[0] + tgt[len(text(base)):]      # self.x in class A -> A.x
        facts.writes.append((line, u.id, tgt))
        # base name is checked against the unit's local names after the walk
        facts_write_bases.append((len(facts.writes) - 1, text(base) if base is not None else ""))

    facts_write_bases: list[tuple[int, str]] = []

    # stack items: node, nesting level, field name in parent, outermost loop span
    stack = [(root, 0, None, None)]
    while stack:
        n, level, fname, loop = stack.pop()
        t = n.type
        if n.is_error or n.is_missing:
            facts.parse_errors += 1
        line = n.start_point[0] + 1
        u = unit_of(n)
        child_level = level
        child_loop = loop

        if n.is_named:
            # ---- names
            if n.child_count == 0:
                if _is_name_leaf(n):
                    u.vocab.add(text(n))
                if t in K.READ_TYPES and n.start_byte not in not_reads and fname not in K.NOT_READ_FIELDS:
                    u.reads.append((text(n), line, n.start_byte, loop[0] if loop else -1, loop[1] if loop else -1))

            # ---- decisions and nesting
            is_if = bool(K.IF_RE.match(t))
            is_loop = bool(K.LOOP_RE.match(t)) and t not in K.LOOP_EXCLUDE
            is_case = t in K.CASE_TYPES and not text(n).lstrip().startswith("default")
            block = is_if or is_loop or is_case or t in K.TERNARY_TYPES or bool(K.CATCH_RE.match(t))
            if block:
                parent = n.parent
                continuation = is_if and parent is not None and (
                    bool(K.IF_RE.match(parent.type)) or (
                        "else" in parent.type and parent.parent is not None and bool(K.IF_RE.match(parent.parent.type))))
                depth = level - 1 if continuation else level
                facts.decisions.append((line, u.id, 1))
                facts.nesting.append((line, u.id, max(depth, 0)))
                child_level = level if continuation else level + 1
                if is_loop:
                    child_loop = loop or (n.start_byte, n.end_byte)
                    for f in ("left", "pattern", "name"):
                        target(n.child_by_field_name(f), u, line, True, False)
            elif K.EXIT_RE.match(t) and "type" not in t:
                if n.start_byte not in u.final_bytes:
                    facts.decisions.append((line, u.id, 1))
                    facts.nesting.append((line, u.id, level))

            # ---- a function or class defined inside another function is a local name there, and its parameters
            # (like a lambda's) are parameters of the enclosing unit for the hidden-flow and write checks
            nested_def = K.GENERIC_DEF_RE.match(t) and n.start_byte not in piece_starts
            if nested_def:
                local = n.child_by_field_name("name")
                if local is not None and _is_name_leaf(local):
                    u.assigns.setdefault(text(local), []).append((line, n.start_byte))
            if nested_def or t in K.LAMBDA_TYPES:
                plist = n.child_by_field_name("parameters")
                for c in (plist.named_children if plist is not None else []):
                    pname = _param_name(c, text)
                    if pname:
                        u.params.add(pname)
                        u.assigns.setdefault(pname, []).append((line, c.start_byte))

            # ---- concurrency
            if "await" in t or t in K.CONCURRENCY_TYPES:
                facts.concurrency.append((line, u.id, 1))

            # ---- assignments and declarations
            if K.ASSIGN_RE.search(t) and "pattern" not in t:
                left = n.child_by_field_name("left") or (n.named_children[0] if n.named_children else None)
                augmented = "augmented" in t or "compound" in t or "operator_assignment" in t
                target(left, u, line, False, augmented)
            elif K.DECLARATOR_RE.search(t):
                tgt = next((n.child_by_field_name(f) for f in K.TARGET_FIELDS
                            if n.child_by_field_name(f) is not None), None)
                target(tgt, u, line, True, False)

            # ---- calls
            if K.CALL_RE.search(t):
                callee = next((n.child_by_field_name(f) for f in K.CALLEE_FIELDS
                               if n.child_by_field_name(f) is not None), None)
                if callee is not None:
                    inner = callee
                    while inner.type == "parenthesized_expression" and inner.named_children:
                        inner = inner.named_children[0]
                    # (function () {...})() runs code written right there: not hidden
                    runtime = (inner.type in K.INDEX_TYPES or bool(K.CALL_RE.search(inner.type))) and \
                        inner.type not in K.LAMBDA_TYPES
                    has_receiver = any(n.child_by_field_name(f) is not None for f in ("object", "receiver"))
                    receiver = n.child_by_field_name("object") or n.child_by_field_name("receiver")
                    via_super = text(callee).startswith("super") or (
                        receiver is not None and text(receiver).startswith("super"))
                    if via_super:                 # super().m(): goes to the parent class, no search needed
                        name, plain = None, False
                    elif runtime:
                        name, plain = None, False
                    elif _is_name_leaf(callee):
                        name, plain = text(callee), not has_receiver
                    else:
                        leaf = _last_name_leaf(callee)
                        name, plain = (text(leaf) if leaf is not None else None), False
                    rec_node = receiver
                    if rec_node is None and K.MEMBER_RE.search(callee.type):
                        rec_node = next((callee.child_by_field_name(f) for f in K.RECEIVER_FIELDS
                                         if callee.child_by_field_name(f) is not None), None)
                    rec_text = " ".join(text(rec_node).split())[:60] if rec_node is not None else None
                    facts.calls.append(Call(line, u.id, name, runtime, plain, rec_text))
                    if runtime:
                        facts.hidden.append((line, u.id, "call picked at run time: " + " ".join(text(callee).split())[:60]))
                    if name in K.CONCURRENCY_CALLS:
                        facts.concurrency.append((line, u.id, 1))
                    if name in ("require", "require_relative"):
                        facts.import_text += text(n) + "\n"

            # ---- hidden flow: decorators, functions handed to other code
            if t in K.DECORATOR_TYPES:
                facts.hidden.append((line, u.id, "decorator: " + " ".join(text(n).split())[:60]))
            elif t in K.LAMBDA_TYPES:
                parent = n.parent
                handed = t == "do_block" or (parent is not None and (
                    bool(K.ARGUMENT_LIST_RE.match(parent.type)) or parent.type in K.HANDOFF_PARENT_TYPES))
                if handed:
                    facts.hidden.append((line, u.id, "function passed as a value"))
            if K.ARGUMENT_LIST_RE.match(t):
                for c in n.named_children:
                    if c.type == "keyword_argument":
                        c = c.child_by_field_name("value") or c
                    if _is_name_leaf(c):
                        facts.fn_args.append((c.start_point[0] + 1, unit_of(c).id, text(c)))

            if K.IMPORT_RE.search(t):
                facts.import_text += text(n) + "\n"

        # ---- operator and keyword tokens: and/or, async
        for c in n.children:
            if not c.is_named:
                if c.type in K.BOOL_TOKENS:
                    facts.decisions.append((c.start_point[0] + 1, u.id, 1))
                    facts.nesting.append((c.start_point[0] + 1, u.id, level))
                elif c.type in K.CONCURRENCY_TOKENS and c.type != "await":
                    facts.concurrency.append((c.start_point[0] + 1, u.id, 1))

        children = n.children
        for i in range(len(children) - 1, -1, -1):
            c = children[i]
            f = n.field_name_for_child(i)
            lvl = level if (block and f in K.CONDITION_FIELDS) else child_level
            stack.append((c, lvl, f, child_loop))

    # ---- after the walk: things that need a unit's full set of names
    file_units = {u.id: u for u in units if u.file == facts.path}
    for idx, base in facts_write_bases:
        line, uid, tgt = facts.writes[idx]
        u = file_units[uid]
        local = base in u.assigns and base not in u.params
        if local:
            facts.writes[idx] = (line, uid, "")
    facts.writes = [w for w in facts.writes if w[2]]
    for call in facts.calls:
        u = file_units[call.unit]
        if call.name and u.kind == "function" and call.name == u.simple:
            facts.decisions.append((call.line, u.id, 1))          # recursion
        if call.plain and call.name in u.params:
            facts.hidden.append((call.line, u.id, f"calls a function passed in: {call.name}()"))
    for u in file_units.values():
        for name, line, byte, lo, hi in u.reads:
            sites = u.assigns.get(name)
            if not sites:
                continue
            count = sum(1 for s_line, s_byte in sites if s_line < line or (lo <= s_byte < hi))
            if count:
                facts.datadeps.append((line, u.id, count))
        u.reads = []


# --------------------------------------------------------------------------------------------------------------

def read_repo(root: str, use_cache: bool = True) -> Repo:
    """Read every file of the project at `root` (see files.py for which files belong to it).

    The parsed result is cached in the user's cache folder (~/.cache/repo-analyzer/facts/), never inside the
    project, and reused while no file changed and the reader version is the same. Only this program writes it."""
    root = os.path.abspath(root)
    listing = list_project_files(root)
    key = (READER_VERSION, root, listing.fingerprint())
    cache = CACHE_DIR / (hashlib.sha256(root.encode("utf-8", "surrogateescape")).hexdigest()[:32] + ".pkl")
    if use_cache and cache.exists():
        try:
            with open(cache, "rb") as fh:
                stored_key, repo = pickle.load(fh)
            if stored_key == key:
                return repo
        except Exception:                          # noqa: BLE001 - a broken or old cache is simply rebuilt
            pass
    repo = _read(root, listing)
    if use_cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, "wb") as fh:
                pickle.dump((key, repo), fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cache)
        except OSError:
            pass                                   # the cache only saves time; reading works without it
    return repo


def _read(root: str, listing: Listing) -> Repo:
    files: dict[str, FileFacts] = {}
    units: list[Unit] = []
    binary, unreadable = [], []
    for path in listing.files:
        try:
            raw = _read_text(os.path.join(root, path))
        except OSError:
            unreadable.append(path)
            continue
        if raw is None:
            binary.append(path)
            continue
        text_lines = raw.split(b"\n")
        if text_lines and text_lines[-1] == b"":
            text_lines.pop()
        nonblank = [bool(l.strip()) for l in text_lines]
        language = _detect_language(path)
        facts = FileFacts(path, language, False, nonblank)
        tree = None
        parser = _parser(language) if language else None
        if parser is not None and nonblank:
            try:
                tree = parser.parse(raw)
            except Exception:                      # noqa: BLE001 - a file the parser rejects is counted by lines
                tree = None
        if tree is not None:
            def text(node, _raw=raw) -> str:
                return _raw[node.start_byte:node.end_byte].decode("utf-8", "replace")
            try:
                facts.pieces = _pieces(_definitions(language, tree.root_node, text), text)
                facts.analyzed = bool(facts.pieces) or _tags_query(language) is not None
            except Exception:                      # noqa: BLE001 - never let one odd file stop the run
                facts.pieces, facts.analyzed = {}, False
            if not facts.analyzed:
                facts.pieces = {}
        _make_units(path, facts, units)
        if facts.analyzed:
            try:
                _setup_function_units(facts, units, text)
                _analyze(facts, tree.root_node, raw, units)
            except RecursionError:
                pass
        for p in facts.pieces.values():
            p.nodes = []                           # syntax nodes are only needed while reading
        files[path] = facts
    return Repo(root, files, units, listing.source, listing.skipped_large, binary, unreadable)


def pieces_listing(repo: Repo) -> str:
    """The list of exact piece names a component map may use, one block per file."""
    out = ["# Named pieces", "",
           "Every function, class and method the reader found, with its line span. Use these exact names in",
           "`path::Name` entries. A file listed with no pieces can only be assigned whole, or by line range.", ""]
    for path, f in sorted(repo.files.items()):
        lang = f.language or "unknown"
        out.append(f"## {path}  ({lang}, {f.code_lines} non-blank lines)")
        if not f.pieces:
            out.append("- (no named pieces)")
        for p in sorted(f.pieces.values(), key=lambda p: (p.spans[0][0], p.name)):
            spans = ", ".join(f"L{s}-L{e}" for s, e in p.spans)
            out.append(f"- `{p.name}`  {p.kind}  {spans}")
        out.append("")
    return "\n".join(out)
