from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CmdResult:
    code: int
    stdout: str
    stderr: str


def run(cmd: list[str], cwd: Path, timeout_s: int = 120) -> CmdResult:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    return CmdResult(code=p.returncode, stdout=p.stdout, stderr=p.stderr)


def git_status_porcelain(cwd: Path) -> str:
    r = run(["git", "status", "--porcelain=v1"], cwd)
    if r.code != 0:
        raise RuntimeError(r.stderr.strip() or "git status 失败")
    return r.stdout


def git_diff(cwd: Path, args: list[str]) -> str:
    r = run(["git", "diff", *args], cwd, timeout_s=120)
    if r.code != 0:
        raise RuntimeError(r.stderr.strip() or "git diff 失败")
    return r.stdout


def git_apply_patch(cwd: Path, patch_text: str, check: bool = True) -> None:
    """
    应用 unified diff patch。
    - check=True：先 git apply --check
    """
    if check:
        r = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=str(cwd),
            input=patch_text,
            text=True,
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "git apply --check 失败")

    r2 = subprocess.run(
        ["git", "apply", "-"],
        cwd=str(cwd),
        input=patch_text,
        text=True,
        capture_output=True,
    )
    if r2.returncode != 0:
        raise RuntimeError(r2.stderr.strip() or "git apply 失败")

