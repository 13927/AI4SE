from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )


def is_git_repo(cwd: Path) -> bool:
    p = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return p.returncode == 0 and p.stdout.strip() == "true"


def git_init(cwd: Path) -> None:
    p = _run_git(["init"], cwd)
    if p.returncode != 0:
        raise RuntimeError(f"git init 失败：{p.stderr.strip()}")


def head_commit(cwd: Path) -> str:
    """
    返回 HEAD commit hash；若尚无 commit，返回 'UNCOMMITTED'。
    """
    p = _run_git(["rev-parse", "HEAD"], cwd)
    if p.returncode != 0:
        return "UNCOMMITTED"
    return p.stdout.strip() or "UNCOMMITTED"


def changed_files(cwd: Path, base: str, head: str) -> list[str]:
    """
    返回 base..head 间变更文件（相对 repo 根路径，使用 / 分隔）。
    """
    p = _run_git(["diff", "--name-only", f"{base}..{head}"], cwd)
    if p.returncode != 0:
        raise RuntimeError(f"git diff 失败：{p.stderr.strip()}")
    files = [line.strip() for line in p.stdout.splitlines() if line.strip()]
    return [f.replace("\\", "/") for f in files]


def ensure_git(cwd: Path) -> None:
    if not is_git_repo(cwd):
        git_init(cwd)


def add_files(cwd: Path, files: Iterable[str]) -> None:
    p = _run_git(["add", *files], cwd)
    if p.returncode != 0:
        raise RuntimeError(f"git add 失败：{p.stderr.strip()}")

