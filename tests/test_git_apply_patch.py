from __future__ import annotations

import subprocess
from pathlib import Path

from aise.git_tools import git_apply_patch


def _git(cwd: Path, *args: str) -> None:
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    assert p.returncode == 0, p.stderr


def test_git_apply_patch(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "init")

    patch = (
        "diff --git a/a.txt b/a.txt\n"
        "index ce01362..94954ab 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-hello\n"
        "+hello world\n"
    )
    git_apply_patch(tmp_path, patch_text=patch, check=True)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello world\n"

