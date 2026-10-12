"""Which files belong to a project."""

import os

from repo_analyzer import config as K
from repo_analyzer.files import list_project_files

from conftest import git_init, write_files


def test_folder_walk_honours_gitignore_and_skips_dependencies(tmp_path):
    root = write_files(tmp_path / "p", {
        "app.py": "x = 1\n",
        ".gitignore": "*.log\nbuild-out/\n",
        "debug.log": "noise\n",
        "build-out/x.js": "built\n",
        "node_modules/lib/index.js": "module.exports = 1\n",
        ".venv/lib/site.py": "x = 1\n",
        "sub/.gitignore": "secret.txt\n",
        "sub/secret.txt": "hidden\n",
        "sub/keep.txt": "kept\n",
        f"{K.WORK_DIR}/map.json": "{}\n",
    })
    listing = list_project_files(str(root))
    assert listing.source == "folder"
    assert listing.files == [".gitignore", "app.py", "sub/.gitignore", "sub/keep.txt"]


def test_git_checkout_uses_git_rules(tmp_path):
    root = write_files(tmp_path / "g", {"a.py": "a = 1\n", ".gitignore": "ignored.txt\n"})
    git_init(root)
    write_files(root, {"new.py": "b = 2\n", "ignored.txt": "x\n"})
    listing = list_project_files(str(root))
    assert listing.source == "git"
    assert listing.files == [".gitignore", "a.py", "new.py"]


def test_symlinks_and_large_files_are_left_out(tmp_path, monkeypatch):
    outside = write_files(tmp_path / "outside", {"secret.py": "s = 1\n"})
    root = write_files(tmp_path / "p", {"a.py": "a = 1\n", "big.txt": "x" * 200})
    os.symlink(outside / "secret.py", root / "link.py")
    os.symlink(outside, root / "linkdir")
    monkeypatch.setattr(K, "MAX_FILE_BYTES", 100)
    listing = list_project_files(str(root))
    assert listing.files == ["a.py"]
    assert listing.skipped_large == ["big.txt"]


def test_fingerprint_changes_when_a_file_changes(tmp_path):
    root = write_files(tmp_path / "p", {"a.py": "a = 1\n"})
    before = list_project_files(str(root)).fingerprint()
    (root / "a.py").write_text("a = 22\n")
    assert list_project_files(str(root)).fingerprint() != before
