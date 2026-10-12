"""The installer: runnable as ./install.sh after a clone, valid bash, and its options."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "install.sh"


def test_installer_is_executable_and_valid_bash():
    assert os.access(SCRIPT, os.X_OK)                        # the README runs it as ./install.sh
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_installer_options(tmp_path):
    shown = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True)
    assert shown.returncode == 0 and "--uninstall" in shown.stdout
    wrong = subprocess.run([str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert wrong.returncode == 1 and "unknown option" in wrong.stderr
    # nothing installed in an empty home: uninstall says so and changes nothing
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    gone = subprocess.run([str(SCRIPT), "--uninstall"], capture_output=True, text=True, env=env)
    assert gone.returncode == 0 and "not installed" in gone.stdout
    assert list(tmp_path.iterdir()) == []
