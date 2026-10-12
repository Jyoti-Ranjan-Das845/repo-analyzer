"""Reading and resolving component maps."""

import copy
import json

from repo_analyzer.contract import EXCLUDED, TESTS, check, from_dict, resolve
from repo_analyzer.reader import read_repo

from conftest import GOOD_MAP


def kinds(problems):
    return sorted(p.kind for p in problems)


def test_good_map_has_no_problems(shop, tmp_path):
    repo = read_repo(str(shop))
    path = tmp_path / "map.json"
    path.write_text(json.dumps(GOOD_MAP))
    contract, res, problems, _ = check(str(path), repo)
    assert problems == []
    assert set(res.owner["tests/test_cart.py"]) == {TESTS}
    assert EXCLUDED in res.owner["README.md"]


def test_most_specific_entry_wins(shop):
    repo = read_repo(str(shop))
    data = copy.deepcopy(GOOD_MAP)
    data["components"][0]["subcomponents"][0]["owns"] = ["src/shop/"]          # the whole folder
    data["components"][1]["subcomponents"][0]["owns"] = ["src/shop/payments.py"]
    data["components"][2]["subcomponents"][0]["owns"] = ["src/shop/money.py::round_cents"]
    contract, problems = from_dict(data)
    res = resolve(contract, repo)
    assert problems == [] and res.problems == []
    money = res.owner["src/shop/money.py"]
    f = repo.files["src/shop/money.py"]
    lo, hi = f.pieces["round_cents"].spans[0]
    assert set(money[lo - 1:hi]) == {"c3s1"}
    assert money[0] == "c1s1"                                                 # format_money: from the folder
    assert set(res.owner["src/shop/payments.py"]) == {"c2s1"}


def test_problems_are_structured(shop):
    repo = read_repo(str(shop))
    data = copy.deepcopy(GOOD_MAP)
    data["components"][0]["subcomponents"][0]["owns"] = ["src/shop/cart.py", "src/shop/nope.py",
                                                         "src/shop/money.py::format_monee", "/etc/passwd"]
    data["components"][1]["subcomponents"][0]["owns"].append("src/shop/cart.py")       # second owner, same level
    data["components"][2]["subcomponents"][0]["owns"] = ["src/shop/money.py::round_cents"]
    contract, structural = from_dict(data)
    res = resolve(contract, repo)
    found = kinds(res.problems)
    assert found.count("bad_entry") == 3
    assert "conflict" in found
    assert "uncovered_file" in found                                          # src/shop/__init__.py
    assert "unassigned_piece" in found                                        # money.py::format_money
    conflict = next(p for p in res.problems if p.kind == "conflict")
    assert conflict.owner == "c2s1" and conflict.entry == "src/shop/cart.py"  # the later entry is the loser
    hint = next(p for p in res.problems if p.entry == "src/shop/money.py::format_monee")
    assert "format_money" in hint.message


def test_structure_problems(shop):
    contract, problems = from_dict({"components": [{"id": "c1", "name": "A", "kind": "weird",
                                                    "description": "d", "subcomponents": []}]})
    messages = " ".join(p.message for p in problems)
    assert "kind must be one of" in messages and "at least one subcomponent" in messages
    assert from_dict([1, 2])[0] is None
