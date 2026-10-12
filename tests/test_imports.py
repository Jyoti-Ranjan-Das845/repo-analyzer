"""Import lines matched to project files, the same way for every language, and calls linked through them."""

from repo_analyzer.contract import from_dict, resolve
from repo_analyzer.imports import read_imports
from repo_analyzer.metrics import Scorer, score
from repo_analyzer.reader import read_repo

from conftest import write_files


def imports_of(tmp_path, files):
    repo = read_repo(str(write_files(tmp_path / "p", files)), use_cache=False)
    return read_imports(repo)


def test_javascript_paths_folders_and_packages(tmp_path):
    imp = imports_of(tmp_path, {
        "index.js": "exports.json = require('./lib/types/json')\n",
        "lib/types/json.js": "function json () {}\nmodule.exports = json\n",
        "lib/read.js": "const { a, b } = require('./util')\nconst debug = require('debug')\n",
        "lib/util.js": "function a () {}\nfunction b () {}\n",
        "lib/ui/index.js": "export function ui () {}\n",
        "test/json.js": "var bodyParser = require('..')\nimport { ui } from '../lib/ui'\n",
    })
    assert imp.files["test/json.js"] == {"index.js", "lib/ui/index.js"}      # '..' is the project's main file
    assert imp.files["index.js"] == {"lib/types/json.js"}
    assert imp.names["test/json.js"]["bodyParser"] == {"index.js"}         # x = require(...) brings in x
    assert imp.names["lib/read.js"]["a"] == {"lib/util.js"}
    assert imp.names["lib/read.js"]["debug"] == set()                      # an installed package: outside


def test_python_relative_absolute_and_packages(tmp_path):
    imp = imports_of(tmp_path, {
        "src/pkg/__init__.py": "from .signer import Signer\n",
        "src/pkg/signer.py": "from . import encoding\nfrom ..tools.text import clean\n",
        "src/pkg/encoding.py": "import json\n",
        "src/tools/__init__.py": "",
        "src/tools/text.py": "def clean(s):\n    return s\n",
        "lib/types/json.py": "def json():\n    pass\n",
        "tests/test_signer.py": "from pkg.signer import Signer\nimport subprocess\n",
    })
    assert imp.files["src/pkg/__init__.py"] == {"src/pkg/signer.py"}
    assert imp.files["src/pkg/signer.py"] == {"src/pkg/__init__.py", "src/pkg/encoding.py", "src/tools/text.py",
                                              "src/tools/__init__.py"}
    # importing pkg.signer runs the package's __init__.py first
    assert imp.files["tests/test_signer.py"] == {"src/pkg/signer.py", "src/pkg/__init__.py"}
    assert imp.names["src/pkg/encoding.py"]["json"] == set()               # the standard library, not lib/types
    assert imp.names["tests/test_signer.py"]["subprocess"] == set()


def test_go_rust_java_and_c(tmp_path):
    imp = imports_of(tmp_path, {
        "cmd/app/main.go": 'package main\n\nimport (\n\t"fmt"\n\tu "github.com/acme/app/internal/util"\n)\n',
        "internal/util/strings.go": "package util\n\nfunc Clean(s string) string { return s }\n",
        "internal/util/numbers.go": "package util\n\nfunc Abs(x int) int { return x }\n",
        "src/lib.rs": "mod parser;\nuse crate::parser::parse;\n",
        "src/parser.rs": "use super::lexer;\npub fn parse() {}\n",
        "src/lexer.rs": "pub fn lex() {}\n",
        "src/main/java/com/acme/App.java": "import com.acme.util.Strings;\nimport java.util.List;\nclass App {}\n",
        "src/main/java/com/acme/util/Strings.java": "package com.acme.util;\npublic class Strings {}\n",
        "c/main.c": '#include "util/strings.h"\n#include <stdio.h>\nint main(void) { return 0; }\n',
        "c/util/strings.h": "int clean(int x);\n",
    })
    go = "cmd/app/main.go"
    assert imp.files[go] == {"internal/util/strings.go", "internal/util/numbers.go"}   # a Go package folder
    assert imp.names[go]["u"] == {"internal/util/strings.go", "internal/util/numbers.go"}
    assert imp.names[go]["fmt"] == set()
    assert imp.files["src/lib.rs"] == {"src/parser.rs"}            # parse is an item inside parser.rs
    assert imp.files["src/parser.rs"] == {"src/lexer.rs"}          # super:: is the parent module
    assert imp.files["src/main/java/com/acme/App.java"] == {"src/main/java/com/acme/util/Strings.java"}
    assert imp.files["c/main.c"] == {"c/util/strings.h"}
    assert imp.names["c/main.c"]["stdio"] == set()


# ---------------------------------------------------------------------------------------------------------------
# calls linked through imports

def scorer_for(tmp_path, files, owners, tests=("test/",)):
    root = write_files(tmp_path / "p", files)
    comps = [{"id": f"c{i}", "name": name, "kind": "feature", "description": name,
              "subcomponents": [{"id": f"c{i}s1", "name": name, "description": name, "owns": owns}]}
             for i, (name, owns) in enumerate(owners.items(), 1)]
    contract, problems = from_dict({"repo": "p", "commit": "", "tests": list(tests), "excluded": [],
                                    "components": comps})
    assert problems == []
    repo = read_repo(str(root), use_cache=False)
    res = resolve(contract, repo)
    assert res.errors == []
    s = Scorer(repo, contract, res)
    links = {(s.units[a].name, s.units[b].name) for a, b, *_ in s.edges}
    subs = {sub["name"]: sub for c in score(repo, contract, res)["components"] for sub in c["subcomponents"]}
    return s, links, subs


def test_module_calls_with_short_names_are_linked(tmp_path):
    # the tests call bodyParser.json(): a short name through the package's main file, which passes it on
    _, links, subs = scorer_for(tmp_path, {
        "index.js": "exports.json = require('./lib/json')\nexports.raw = require('./lib/raw')\n",
        "lib/json.js": "function json (opts) {\n  return parse(opts)\n}\nfunction parse (x) {\n  return x\n}\n"
                       "module.exports = json\n",
        "lib/raw.js": "function raw (opts) {\n  return opts\n}\nmodule.exports = raw\n",
        "test/json.js": "var bodyParser = require('..')\nfunction createServer (opts) {\n"
                        "  return bodyParser.json(opts)\n}\n",
    }, {"Entry": ["index.js"], "JSON": ["lib/json.js"], "Raw": ["lib/raw.js"]})
    assert ("createServer", "json") in links
    assert subs["JSON"]["metrics"]["T"] == 1.0              # json and the parse it calls are reached from tests
    assert subs["Raw"]["metrics"]["T"] < 1.0                # nothing calls raw: only its top-level lines run


def test_calls_into_outside_libraries_never_link_to_project_code(tmp_path):
    _, links, subs = scorer_for(tmp_path, {
        "app/run.py": "import subprocess\n\n\ndef run():\n    return subprocess.check_output(['ls'])\n",
        "app/tools.py": "def check_output(cmd):\n    return cmd\n",
    }, {"Run": ["app/run.py"], "Tools": ["app/tools.py"]}, tests=())
    assert ("run", "check_output") not in links             # a distinctive name, but subprocess is a library
    assert subs["Tools"]["metrics"]["R"] == 0


def test_imports_link_across_languages(tmp_path):
    _, links, _ = scorer_for(tmp_path, {
        "src/App.tsx": "import { helper } from './utils'\n\nexport function App() {\n  return helper(1)\n}\n",
        "src/utils.ts": "export function helper(x: number) {\n  return x + 1\n}\n",
    }, {"UI": ["src/App.tsx"], "Utils": ["src/utils.ts"]}, tests=())
    assert ("App", "helper") in links                       # a .tsx file calling a .ts file it imports


def test_an_imported_name_is_not_a_name_clash(tmp_path):
    _, links, subs = scorer_for(tmp_path, {
        "a/format.py": "def render(x):\n    return x\n",
        "b/format.py": "def render(x):\n    return x\n",
        "c/page.py": "from a.format import render\n\n\ndef page():\n    return render(1)\n",
        "d/view.py": "def view():\n    return render(2)\n",
    }, {"A": ["a/"], "B": ["b/"], "Page": ["c/"], "View": ["d/"]}, tests=())
    assert ("page", "render") in links and subs["Page"]["metrics"]["K"] == 0     # the import says which one
    assert subs["View"]["metrics"]["K"] == 1                                     # a plain search: 2 candidates
