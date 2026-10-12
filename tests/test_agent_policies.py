"""The agent definition: what the shell may run, what may be written, and how progress is read."""

import re
from pathlib import Path

import pytest

from repo_analyzer.omnigent import (SHELL_ALLOW_RE, SHELL_DENY_RE, AgentError, checker_command, events_from_item,
                                    write_bundle)


def shell_allowed(command: str, checker: str = "CHECK") -> bool:
    """The same decision the CEL policy makes (RE2 and Python agree on these patterns)."""
    if command == checker:
        return True
    return not re.search(SHELL_DENY_RE, command) and re.search(SHELL_ALLOW_RE, command) is not None


@pytest.mark.parametrize("command", [
    "git ls-files", "git ls-files src | grep -E '\\.py$' | head", "git grep -n 'def main' -- src",
    "grep -rn 'Cart' src", "ls -R", "wc -l .repo-analyzer/task/pieces.md", "head -n 40 README.md",
    "cat src/shop/cart.py", "git log --oneline -5", "grep -n '^## src/a.py' .repo-analyzer/task/pieces.md",
])
def test_read_only_searches_are_allowed(command):
    assert shell_allowed(command)


@pytest.mark.parametrize("command", [
    "python3 -c 'print(1)'", "rm -rf src", "cat /etc/passwd", "cat ~/.ssh/id_rsa", "cat ../other/file",
    "ls; rm x", "grep x src > out.txt", "git log --output=x", "cat a | sh", "echo $(whoami)",
    "git -C /tmp ls-files", "sort -o out.txt a", "curl http://example.com", "find . -delete",
    "git grep -O x", "head a && rm a",
])
def test_everything_else_is_refused(command):
    assert not shell_allowed(command)


def test_bundle_restricts_writes_to_map_and_notes(tmp_path):
    project = tmp_path / "p"
    work = project / ".repo-analyzer"
    work.mkdir(parents=True)
    checker = checker_command(project, work / "map.json")
    write_bundle(work / "agent", "do it", project, [work / "map.json", work / "notes.md"], checker)
    config = (work / "agent" / "config.yaml").read_text()
    assert "reasoning_effort:" in config and "skills: none" in config
    assert f'"{work / "map.json"}"' in config and '".repo-analyzer/notes.md"' in config
    assert checker in config
    assert (work / "agent" / "instructions.md").read_text() == "do it"


def test_paths_the_policies_cannot_express_are_refused(tmp_path):
    project = tmp_path / 'with"quote'
    work = project / ".repo-analyzer"
    work.mkdir(parents=True)
    with pytest.raises(AgentError):
        write_bundle(work / "agent", "x", project, [work / "map.json"], "check")


def test_progress_events_skip_duplicates_and_tool_loading():
    runner = {"agent": "repo_analyzer_mapper", "name": "sys_os_read", "arguments": '{"path": "src/a.py"}'}
    model_copy = {"agent": "resp_9", "name": "sys_os_read", "arguments": '{"path": "src/a.py"}'}
    loader = {"agent": "repo_analyzer_mapper", "name": "ToolSearch", "arguments": '{"query": "x"}'}
    text = {"role": "assistant", "content": [{"type": "output_text", "text": "Reading the cart."}]}
    assert events_from_item(runner) == [{"kind": "tool", "tool": "read", "detail": "src/a.py"}]
    assert events_from_item(model_copy) == [] and events_from_item(loader) == []
    assert events_from_item(text) == [{"kind": "text", "text": "Reading the cart."}]
