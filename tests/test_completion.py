"""Deterministic completion always produces a valid map."""

import copy
import json

from repo_analyzer.completion import UNASSIGNED_ID, complete
from repo_analyzer.contract import EXCLUDED, from_dict, resolve
from repo_analyzer.reader import read_repo

from conftest import GOOD_MAP


def run(shop, tmp_path, data):
    path = tmp_path / "map.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    repo = read_repo(str(shop))
    result, contract, res, notes = complete(str(path), repo, "shop", "")
    again = resolve(from_dict(result)[0], repo)
    assert again.problems == [], again.errors                 # the completed map is always valid
    return result, notes


def test_good_map_needs_no_corrections(shop, tmp_path):
    result, notes = run(shop, tmp_path, GOOD_MAP)
    assert notes == []
    assert [c["id"] for c in result["components"]] == ["c1", "c2", "c3"]


def test_broken_map_is_fixed_and_explained(shop, tmp_path):
    data = copy.deepcopy(GOOD_MAP)
    data["components"][0]["subcomponents"][0]["owns"] = ["src/shop/cart.py", "src/shop/nope.py"]
    data["components"][1]["subcomponents"].append({"id": "c2s2", "name": "Twice", "description": "d",
                                                   "owns": ["src/shop/payments.py"]})
    data["components"][2]["subcomponents"][0]["owns"] = []
    data["excluded"] = []
    result, notes = run(shop, tmp_path, data)
    text = " ".join(notes)
    assert "naming no existing" in text and "another owner" in text and "uncovered file" in text
    owners = {o for c in result["components"] for s in c["subcomponents"] for o in s["owns"]}
    assert "src/shop/money.py" in owners                    # code went to a code owner
    assert "src/shop/__init__.py" in owners
    assert all(e["path"] != "src/shop/money.py" for e in result["excluded"])


def test_unreadable_map_becomes_an_unassigned_map(shop, tmp_path):
    result, notes = run(shop, tmp_path, "{not json")
    assert "rebuilt" in " ".join(notes)
    assert [c["id"] for c in result["components"]] == [UNASSIGNED_ID]


def test_shape_problems_are_normalized(shop, tmp_path):
    data = {"components": [
        {"id": "c1", "name": "", "kind": "nonsense", "owns": ["src/"]},          # no subcomponents, bad kind
        {"id": "c1", "name": "Copy", "kind": "feature", "description": "dup id",
         "subcomponents": [{"name": "x", "owns": ["tests/"]}]},
    ], "tests": "not a list"}
    result, notes = run(shop, tmp_path, data)
    ids = [c["id"] for c in result["components"]]
    assert len(set(ids)) == len(ids)
    assert all(c["kind"] in ("feature", "foundation", "supporting") for c in result["components"])


def test_non_code_neighbour_goes_with_excluded(shop, tmp_path):
    (shop / "CHANGES.md").write_text("# changes\n")
    data = copy.deepcopy(GOOD_MAP)
    result, notes = run(shop, tmp_path, data)
    assert any(e["path"] == "CHANGES.md" for e in result["excluded"])        # next to README.md, also excluded
