"""Scores and the report page."""

import json

import pytest

from repo_analyzer import config as K
from repo_analyzer.contract import from_dict, resolve
from repo_analyzer.html_report import build_html
from repo_analyzer.metrics import score
from repo_analyzer.reader import read_repo
from repo_analyzer.report import render

from conftest import GOOD_MAP


@pytest.fixture
def scored(shop):
    repo = read_repo(str(shop))
    contract, problems = from_dict(GOOD_MAP)
    assert problems == []
    res = resolve(contract, repo)
    return score(repo, contract, res)


def by_id(result):
    out = {}
    for c in result["components"]:
        out[c["id"]] = c
        for s in c["subcomponents"]:
            out[s["id"]] = s
    return out


def test_effort_is_the_sum_of_its_parts(scored):
    for owner in [scored["repository"], *by_id(scored).values()]:
        m, p = owner["metrics"], owner["parts"]
        assert p["understanding"] == pytest.approx((m["L"] + m["X"]) * m["C"], rel=1e-2)
        assert p["noise"] == m["N"]
        assert p["finding"] == K.TRIP_COST * (m["F"] + m["H"] + m["K"] + m["E"] + m["D"])
        assert p["checking"] == pytest.approx((m["L"] + m["R"]) * (1 - m["T"]), rel=1e-2, abs=1)
        assert owner["effort"] == pytest.approx(sum(p.values()), abs=1)


def test_links_between_parts(scored):
    owners = by_id(scored)
    money, cart, pay = owners["c3s1"], owners["c1s1"], owners["c2s1"]
    assert {u["name"] for u in cart["evidence"]["outside"]} >= {"round_cents", "format_money"}
    assert cart["metrics"]["X"] > 0 and money["metrics"]["R"] > 0     # the cart uses money; money is used
    assert cart["metrics"]["T"] > 0.5                                  # tests reach Cart via Cart() and c.add/total
    # no test calls payments: only its top-level lines run, because the test imports the shop package
    assert 0 < pay["metrics"]["T"] < 0.2
    assert pay["metrics"]["D"] >= 2                                    # decorator + handlers[...]()
    assert scored["repository"]["metrics"]["X"] == 0 and scored["repository"]["metrics"]["R"] == 0


def test_markdown_and_html_report(scored):
    md = render(scored)
    assert "Shopping" in md and "Effort" in md
    scored["run"] = {"name": "</script><script>alert(1)</script>", "files": 1}
    page = build_html(scored, GOOD_MAP)
    assert page.count("</script>") == 2                                 # the data cannot close the script element
    data = page.split('id="report-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(data)["score"]["run"]["name"].startswith("</script>")
