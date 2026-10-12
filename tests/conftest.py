"""Shared test fixtures: small sample projects and a fake Omnigent (tests/fake_omni.py)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from repo_analyzer import config as K
from repo_analyzer import reader

HERE = Path(__file__).resolve().parent

SHOP = {
    "README.md": "# shop\nA small shop: cart, pricing and payments.\n",
    "src/shop/__init__.py": "from .cart import Cart\nfrom .payments import charge\n",
    "src/shop/money.py": (
        "def format_money(value):\n"
        "    if value < 0:\n"
        "        return \"-\" + format_money(-value)\n"
        "    return f\"${value:,.2f}\"\n\n\n"
        "def round_cents(value):\n"
        "    return round(value * 100) / 100\n"),
    "src/shop/cart.py": (
        "from .money import format_money, round_cents\n\n\n"
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "        self.discount = 0\n\n"
        "    def add(self, name, price, qty=1):\n"
        "        if qty <= 0:\n"
        "            raise ValueError(\"qty must be positive\")\n"
        "        self.items.append((name, price, qty))\n\n"
        "    def total(self):\n"
        "        subtotal = sum(p * q for _, p, q in self.items)\n"
        "        return round_cents(subtotal * (1 - self.discount))\n\n"
        "    def apply_coupon(self, code):\n"
        "        if code == \"HALF\":\n"
        "            self.discount = 0.5\n"
        "        elif code.startswith(\"TEN\"):\n"
        "            self.discount = 0.1\n"
        "        else:\n"
        "            return False\n"
        "        return True\n\n"
        "    def summary(self):\n"
        "        return format_money(self.total())\n"),
    "src/shop/payments.py": (
        "import functools\n\nLOG = []\n\n\n"
        "def retry(times):\n"
        "    def wrap(fn):\n"
        "        @functools.wraps(fn)\n"
        "        def inner(*a, **k):\n"
        "            for attempt in range(times):\n"
        "                try:\n"
        "                    return fn(*a, **k)\n"
        "                except ConnectionError:\n"
        "                    if attempt == times - 1:\n"
        "                        raise\n"
        "        return inner\n"
        "    return wrap\n\n\n"
        "@retry(3)\n"
        "def charge(cart, account):\n"
        "    amount = cart.total()\n"
        "    if account.balance < amount:\n"
        "        return False\n"
        "    account.balance -= amount\n"
        "    LOG.append((\"charge\", amount))\n"
        "    return True\n\n\n"
        "def refund(account, amount, handlers):\n"
        "    handlers[account.kind](amount)\n"
        "    account.balance += amount\n"),
    "tests/test_cart.py": (
        "from shop.cart import Cart\n\n\n"
        "def test_total():\n"
        "    c = Cart()\n"
        "    c.add(\"a\", 2, 3)\n"
        "    assert c.total() == 6\n"),
    "docs/guide.md": "# Guide\nHow to use the shop.\n",
}

GOOD_MAP = {
    "repo": "shop", "commit": "",
    "tests": ["tests/"],
    "excluded": [{"path": "docs/", "reason": "documentation"}, {"path": "README.md", "reason": "readme"}],
    "components": [
        {"id": "c1", "name": "Shopping", "kind": "feature", "description": "The cart and its prices.",
         "subcomponents": [
             {"id": "c1s1", "name": "Cart", "description": "Holds items and totals.",
              "owns": ["src/shop/cart.py", "src/shop/__init__.py"]}]},
        {"id": "c2", "name": "Payments", "kind": "feature", "description": "Charging and refunds.",
         "subcomponents": [
             {"id": "c2s1", "name": "Charging", "description": "Charge and refund accounts.",
              "owns": ["src/shop/payments.py"]}]},
        {"id": "c3", "name": "Foundation", "kind": "foundation", "description": "Money helpers.",
         "subcomponents": [
             {"id": "c3s1", "name": "Money", "description": "Formatting and rounding.",
              "owns": ["src/shop/money.py"]}]},
    ],
}


def write_files(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    return root


def git_init(root: Path) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=root, check=True, env=env, capture_output=True)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own parsed-code cache folder."""
    monkeypatch.setattr(reader, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def shop(tmp_path) -> Path:
    return write_files(tmp_path / "shop", SHOP)


@pytest.fixture
def fake_omni(tmp_path, monkeypatch):
    """Point the agent at tests/fake_omni.py. Returns a function that writes the scenario and returns the log."""
    script = tmp_path / "bin" / "omni"
    script.parent.mkdir()
    script.write_text(f"#!/bin/sh\nexec \"{sys.executable}\" \"{HERE / 'fake_omni.py'}\" \"$@\"\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    scenario = tmp_path / "scenario.json"
    monkeypatch.setenv(K.OMNI_PATH_ENV, str(script))
    monkeypatch.setenv("FAKE_OMNI_SCENARIO", str(scenario))
    monkeypatch.setattr(K, "AGENT_CLAUDE_PATH", str(script))
    monkeypatch.setattr(K, "AGENT_PREVENT_IDLE_SLEEP", False)
    monkeypatch.setattr(K, "AGENT_RETRY_FIRST_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(K, "AGENT_TRANSIENT_RETRY_MINUTES", 0.02)

    def setup(sessions: list[dict], preflight: str = "ok") -> Path:
        scenario.write_text(json.dumps({"preflight": preflight, "sessions": sessions}))
        return Path(str(scenario) + ".log")

    return setup


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
