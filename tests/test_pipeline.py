"""The whole run, with a fake Omnigent that can succeed, stop early, fail, hang or be interrupted."""

import copy
import json
import os
import signal
import threading

import pytest

from repo_analyzer import config as K
from repo_analyzer.cli import main
from repo_analyzer.pipeline import AnalysisError, Interrupted, Options, analyze
from repo_analyzer.workspace import Workspace

from conftest import GOOD_MAP, calls


def state_of(project):
    return Workspace(project).load_state()


def run(project, **opts):
    return analyze(project, Options(**opts), out=lambda s: None)


def test_happy_path(shop, fake_omni):
    log = fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}])
    report = run(shop)
    ws = Workspace(shop)
    assert report == ws.report_html_path and report.exists()
    for path in (ws.score_path, ws.report_md_path, ws.final_map_path, ws.map_path, ws.state_path, ws.log_path):
        assert path.exists(), path
    state = state_of(shop)
    assert state.status == "done" and state.sessions == 1 and state.agent_status == "complete"
    assert state.corrections == []
    assert [c["bundle"] for c in calls(log)] == ["preflight", "agent"]
    agent_cwds = {c["cwd"] for c in calls(log) if c["bundle"] == "agent"}
    assert agent_cwds == {str(shop.resolve())}                                # the agent works in the project folder
    assert (ws.dir / ".gitignore").read_text() == "*\n"
    assert not ws.lock_path.exists()
    # nothing outside .repo-analyzer was created
    created = {p.relative_to(shop).parts[0] for p in shop.rglob("*")}
    assert created == {"README.md", "src", "tests", "docs", ".repo-analyzer"}


def test_sessions_are_chained_until_the_map_is_complete(shop, fake_omni):
    log = fake_omni([{"action": "notes"}, {"action": "write", "map": GOOD_MAP, "done": True}])
    run(shop)
    agent_calls = [c for c in calls(log) if c["bundle"] == "agent"]
    assert len(agent_calls) == 2
    assert agent_calls[0]["kickoff"].startswith("Start")
    assert agent_calls[1]["kickoff"].startswith("Continue")               # resumes from the saved notes
    assert state_of(shop).sessions == 2


def test_network_failure_is_retried(shop, fake_omni):
    log = fake_omni([{"action": "transient"}, {"action": "write", "map": GOOD_MAP, "done": True}])
    run(shop)
    assert state_of(shop).agent_status == "complete"
    assert len([c for c in calls(log) if c["bundle"] == "agent"]) == 2


def test_stalled_session_is_stopped_and_continued(shop, fake_omni, monkeypatch):
    monkeypatch.setattr(K, "AGENT_STALL_SECONDS", 1)
    monkeypatch.setattr(K, "AGENT_STALL_CHECK_SECONDS", 1)
    log = fake_omni([{"action": "hang", "seconds": 600}, {"action": "write", "map": GOOD_MAP, "done": True}])
    run(shop)
    assert state_of(shop).status == "done"
    assert len([c for c in calls(log) if c["bundle"] == "agent"]) == 2


def test_budget_runs_out_and_the_map_is_completed(shop, fake_omni):
    partial = copy.deepcopy(GOOD_MAP)
    partial["components"] = partial["components"][:1]                      # payments and money left out
    fake_omni([{"action": "write", "map": partial}])                         # never says MAP COMPLETE
    run(shop, max_sessions=2)
    state = state_of(shop)
    assert state.status == "done" and state.agent_status == "budget" and state.sessions == 2
    assert state.repairs == K.REPAIR_ROUNDS                                  # problems went back to the agent
    assert any("uncovered" in c for c in state.corrections)
    final = json.loads(Workspace(shop).final_map_path.read_text())
    owned = {e for c in final["components"] for s in c["subcomponents"] for e in s["owns"]}
    assert {"src/shop/payments.py", "src/shop/money.py"} <= owned


def test_agent_that_cannot_run_still_gives_a_report(shop, fake_omni):
    fake_omni([{"action": "fail"}])
    report = run(shop)
    state = state_of(shop)
    assert report.exists() and state.status == "done" and state.agent_status == "failed"
    assert any("could not finish" in w for w in state.warnings)
    final = json.loads(Workspace(shop).final_map_path.read_text())
    assert [c["id"] for c in final["components"]] == ["unassigned"]


def test_preflight_failure_stops_early(shop, fake_omni):
    log = fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}], preflight="fail")
    with pytest.raises(AnalysisError, match="not working"):
        run(shop)
    assert [c["bundle"] for c in calls(log)] == ["preflight"]
    assert state_of(shop).status == "failed"


def test_interrupted_run_continues(shop, fake_omni, monkeypatch):
    log = fake_omni([{"action": "hang", "seconds": 600}, {"action": "write", "map": GOOD_MAP, "done": True}])
    timer = threading.Timer(1.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    with pytest.raises(Interrupted):
        run(shop)
    timer.cancel()
    assert state_of(shop).status == "interrupted"
    assert not Workspace(shop).lock_path.exists()
    report = run(shop)                                                       # the same command again
    state = state_of(shop)
    assert report.exists() and state.status == "done"
    agent_calls = [c for c in calls(log) if c["bundle"] == "agent"]
    assert len(agent_calls) == 2


def test_finished_run_is_not_repeated_unless_fresh(shop, fake_omni):
    log = fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}])
    run(shop)
    run(shop)
    assert len([c for c in calls(log) if c["bundle"] == "agent"]) == 1
    run(shop, fresh=True)
    assert len([c for c in calls(log) if c["bundle"] == "agent"]) == 2


def test_one_run_at_a_time(shop, fake_omni):
    fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}])
    ws = Workspace(shop)
    ws.create()
    ws.lock_path.write_text(str(os.getppid()))                               # a live process holds the lock
    with pytest.raises(AnalysisError, match="another analysis"):
        run(shop)
    ws.lock_path.write_text("999999999")                                     # a lock left by a dead process
    assert run(shop).exists()


def test_bad_folders(tmp_path, fake_omni):
    fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}])
    with pytest.raises(AnalysisError, match="no such folder"):
        run(tmp_path / "missing")
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(AnalysisError, match="not a folder"):
        run(tmp_path / "file.txt")
    (tmp_path / "empty").mkdir()
    with pytest.raises(AnalysisError, match="no text files"):
        run(tmp_path / "empty")


def test_command_line(shop, fake_omni, capsys):
    fake_omni([{"action": "write", "map": GOOD_MAP, "done": True}])
    assert main(["analyze", str(shop), "--quiet"]) == 0
    assert "report.html" in capsys.readouterr().out
    assert main(["check", str(shop)]) == 0
    assert main(["score", str(shop)]) == 0
    assert main(["analyze", str(shop / "missing")]) == 1
