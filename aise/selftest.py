from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class TargetRepo:
    name: str
    url: str


DEFAULT_TARGETS: list[TargetRepo] = [
    TargetRepo(name="spring-petclinic", url="https://github.com/spring-projects/spring-petclinic"),
    TargetRepo(name="googletest", url="https://github.com/google/googletest"),
]


def _run(cmd: list[str], cwd: Path) -> None:
    # 说明：自测用于 CI，遇到卡住会导致流水线挂死，因此设置 timeout。
    p = subprocess.run(cmd, cwd=str(cwd), text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(
            f"命令失败（cwd={cwd}）：\n"
            f"  {' '.join(cmd)}\n"
            "（输出见上方日志）\n"
        )


def _clone_or_reset(url: str, dest: Path, depth: int = 2) -> None:
    if dest.exists():
        # 重新拉取，避免上次残留状态影响结果
        shutil.rmtree(dest)
    # 网络抖动在 CI 很常见，做 3 次重试；同时用 filter 降低传输量
    last_err: Exception | None = None
    for i in range(3):
        try:
            _run(
                [
                    "git",
                    "clone",
                    "--depth",
                    str(depth),
                    "--filter=blob:none",
                    "--no-tags",
                    "--progress",
                    url,
                    dest.name,
                ],
                cwd=dest.parent,
            )
            return
        except Exception as e:  # noqa: BLE001 - prototype retry
            last_err = e
            # 清理半成品目录
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"clone 失败（重试 3 次仍失败）：{url}") from last_err


def run_selftest(workdir: Path, targets: Iterable[TargetRepo] = DEFAULT_TARGETS) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    for t in targets:
        repo_dir = workdir / t.name
        _clone_or_reset(t.url, repo_dir)

        # 1) init/scan/validate static
        _run(["aise", "init", "--command-name", "aise"], cwd=repo_dir)
        _run(["aise", "scan"], cwd=repo_dir)
        _run(["aise", "validate", "--mode", "static", "--format", "text"], cwd=repo_dir)

        # 最小断言：模块数量阈值（避免退化成只有 core）
        idx_path = repo_dir / "docs/codewiki/L1/index.json"
        if not idx_path.exists():
            raise RuntimeError(f"缺少 index.json：{idx_path}")
        import json

        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        modules = idx.get("modules") or []
        if repo_dir.name == "spring-petclinic" and len(modules) < 5:
            raise RuntimeError(f"模块数过少（spring-petclinic）：{len(modules)}")
        if repo_dir.name == "googletest" and len(modules) < 3:
            raise RuntimeError(f"模块数过少（googletest）：{len(modules)}")

        # Phase 2 最小断言：entrypoints 需非空（googletest/spring-petclinic 期望至少包含一个非默认入口）
        ep_path = repo_dir / "docs/codewiki/views/entrypoints.json"
        if not ep_path.exists():
            raise RuntimeError(f"缺少 entrypoints.json：{ep_path}")
        eps = json.loads(ep_path.read_text(encoding="utf-8")).get("entrypoints") or []
        if repo_dir.name == "spring-petclinic" and len(eps) < 2:
            raise RuntimeError(f"entrypoints 过少（spring-petclinic）：{len(eps)}，期望至少包含 Spring Boot main")
        if repo_dir.name == "googletest" and len(eps) < 2:
            raise RuntimeError(f"entrypoints 过少（googletest）：{len(eps)}，期望至少包含 CMake target")

        # Phase 3 最小断言：apis/flows/playbooks 至少非空（达到“复合预期”雏形）
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        apis = idx.get("apis") or []
        flows = idx.get("flows") or []
        playbooks = idx.get("playbooks") or []
        if repo_dir.name == "spring-petclinic":
            if len(apis) < 3:
                raise RuntimeError(f"apis 过少（spring-petclinic）：{len(apis)}")
            if len(flows) < 1:
                raise RuntimeError("flows 为空（spring-petclinic）")
            if len(playbooks) < 2:
                raise RuntimeError(f"playbooks 过少（spring-petclinic）：{len(playbooks)}")
        if repo_dir.name == "googletest":
            if len(apis) < 1:
                raise RuntimeError(f"apis 过少（googletest）：{len(apis)}")
            if len(flows) < 1:
                raise RuntimeError("flows 为空（googletest）")
            if len(playbooks) < 2:
                raise RuntimeError(f"playbooks 过少（googletest）：{len(playbooks)}")

        # 2) update（增量）与 diff validate（无变更场景）
        # shallow clone depth=2 可用 HEAD~1；若仓库太新导致无 HEAD~1，则跳过
        p = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=str(repo_dir), text=True, capture_output=True)
        if p.returncode == 0:
            _run(["aise", "update", "--base", "HEAD~1", "--head", "HEAD"], cwd=repo_dir)
            _run(["aise", "validate", "--mode", "diff", "--base", "HEAD~1", "--head", "HEAD", "--format", "text"], cwd=repo_dir)
