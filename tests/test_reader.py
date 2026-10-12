"""The reader: pieces, calls, writes, hidden flow and complexity counts, for several languages."""

from repo_analyzer.reader import read_repo

from conftest import write_files


def test_python_pieces_calls_and_facts(shop):
    repo = read_repo(str(shop))
    cart = repo.files["src/shop/cart.py"]
    assert set(cart.pieces) == {"Cart", "Cart.__init__", "Cart.add", "Cart.total", "Cart.apply_coupon",
                                "Cart.summary"}
    assert cart.pieces["Cart"].kind == "class"
    assert any(c.name == "round_cents" and c.plain for c in cart.calls)
    assert {t for _, _, t in cart.writes} >= {"Cart.items", "Cart.discount"}      # self.x named after the class
    assert sum(v for *_, v in cart.decisions) >= 4
    pay = repo.files["src/shop/payments.py"]
    hidden = [w for _, _, w in pay.hidden]
    assert any(w.startswith("decorator") for w in hidden)
    assert any(w.startswith("call picked at run time") for w in hidden)          # handlers[account.kind](amount)
    assert any(w.startswith("calls a function passed in") for w in hidden)        # fn(*a, **k)
    assert repo.files["docs/guide.md"].analyzed is False


def test_javascript_go_java_pieces(tmp_path):
    root = write_files(tmp_path / "p", {
        "web/app.js": ("export class Store {\n  constructor() { this.items = []; }\n"
                       "  add(x) { if (x) { this.items.push(x); } }\n}\n"
                       "export function render(store) { return store.items.map((i) => `<li>${i}</li>`); }\n"),
        "srv/main.go": ("package main\n\ntype Server struct{ n int }\n\n"
                        "func (s *Server) Handle(x int) int {\n\tif x > 1 && s.n > 0 {\n\t\ts.n = x\n\t}\n\treturn s.n\n}\n"),
        "java/Svc.java": ("public class Svc extends Base {\n  public int run(int k) {\n"
                          "    for (int i = 0; i < k; i++) { if (i > 2) { return i; } }\n    return 0;\n  }\n}\n"),
    })
    repo = read_repo(str(root))
    assert {"Store", "render"} <= set(repo.files["web/app.js"].pieces)
    assert "Server.Handle" in repo.files["srv/main.go"].pieces          # Go method qualified by its receiver
    svc = repo.files["java/Svc.java"].pieces
    assert "Svc" in svc and "Base" in svc["Svc"].bases
    assert sum(v for *_, v in repo.files["java/Svc.java"].decisions) >= 3


def test_typescript_and_tsx_pieces(tmp_path):
    # TypeScript's tag rules extend JavaScript's: functions, classes and arrow functions come from JavaScript's
    root = write_files(tmp_path / "p", {
        "src/a.ts": ("export function f(x: number) { return x }\nexport class A { m() { return 1 } }\n"
                     "export const g = () => 1\ninterface Shape { area(): number }\n"),
        "src/B.tsx": "export function B() { return <div/> }\nexport const C = () => <p/>\n",
    })
    repo = read_repo(str(root))
    assert {"f", "A", "A.m", "g", "Shape"} <= set(repo.files["src/a.ts"].pieces)
    assert {"B", "C"} <= set(repo.files["src/B.tsx"].pieces)


def test_unparsable_and_binary_files_do_not_stop_reading(tmp_path):
    root = write_files(tmp_path / "p", {"ok.py": "def f():\n    return 1\n", "broken.py": "def (:\n  ???\n",
                                        "logo.png": b"\x89PNG\x00\x01\x02"})
    repo = read_repo(str(root))
    assert set(repo.files) == {"ok.py", "broken.py"}
    assert repo.skipped_binary == ["logo.png"]


def test_cache_is_reused_and_refreshed(shop):
    first = read_repo(str(shop))
    again = read_repo(str(shop))
    assert set(again.files) == set(first.files)
    (shop / "src/shop/extra.py").write_text("def extra():\n    return 1\n")
    assert "src/shop/extra.py" in read_repo(str(shop)).files
