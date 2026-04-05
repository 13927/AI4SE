from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rich.prompt import Confirm


class Approver(Protocol):
    def approve_plan(self, plan_pretty: str) -> bool: ...
    def approve_upgrade(self, upgrade_pretty: str) -> bool: ...
    def approve_read(self, rel_path: str) -> bool: ...
    def approve_write(self, rel_path: str) -> bool: ...
    def approve_apply_patch(self, touched_files: list[str]) -> bool: ...
    def approve_verify(self, command: str) -> bool: ...
    def approve_write_memory(self, rel_path: str) -> bool: ...


@dataclass
class InteractiveApprover:
    def approve_plan(self, plan_pretty: str) -> bool:
        return Confirm.ask(f"是否批准该执行计划（Plan Contract）？\n{plan_pretty}", default=True)

    def approve_upgrade(self, upgrade_pretty: str) -> bool:
        return Confirm.ask(f"是否批准升级请求？\n{upgrade_pretty}", default=False)

    def approve_read(self, rel_path: str) -> bool:
        return Confirm.ask(f"允许读取文件？\n  {rel_path}", default=False)

    def approve_write(self, rel_path: str) -> bool:
        return Confirm.ask(f"允许写入文件？\n  {rel_path}\n将覆盖/创建该文件。", default=False)

    def approve_apply_patch(self, touched_files: list[str]) -> bool:
        files = "\n".join(f"  - {x}" for x in touched_files[:30])
        if len(touched_files) > 30:
            files += f"\n  ... ({len(touched_files)} files)"
        return Confirm.ask(f"允许应用 patch（git apply）？\n{files}", default=False)

    def approve_verify(self, command: str) -> bool:
        return Confirm.ask(f"允许执行验证命令？\n  {command}", default=False)

    def approve_write_memory(self, rel_path: str) -> bool:
        return Confirm.ask(f"允许写入 memory 文件？\n  {rel_path}", default=False)


def _match_any(path: str, patterns: list[str]) -> bool:
    p = path.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(p, pat):
            return True
    return False


_re_diff_file = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def touched_files_from_patch(patch_text: str) -> list[str]:
    """
    从 unified diff 中抽取变更文件路径。
    """
    out: list[str] = []
    for m in _re_diff_file.finditer(patch_text):
        b = m.group(2)
        if b and b not in out:
            out.append(b)
    return out


@dataclass
class NonInteractiveApprover:
    """
    非交互审批（CI/脚本）：
    - 默认自动批准 plan/upgrade/read/verify（前提：符合 policy）
    - write/apply_patch：必须命中 write_scopes
    """

    read_scopes: list[str]
    write_scopes: list[str]
    verify_allowlist: list[str]

    def approve_plan(self, plan_pretty: str) -> bool:  # noqa: ARG002
        return True

    def approve_upgrade(self, upgrade_pretty: str) -> bool:  # noqa: ARG002
        # 由上层先做 scope 校验，能到这里默认批准
        return True

    def approve_read(self, rel_path: str) -> bool:
        return _match_any(rel_path, self.read_scopes)

    def approve_write(self, rel_path: str) -> bool:
        return _match_any(rel_path, self.write_scopes)

    def approve_apply_patch(self, touched_files: list[str]) -> bool:
        return all(_match_any(p, self.write_scopes) for p in touched_files)

    def approve_verify(self, command: str) -> bool:
        cmd = command.strip()
        return any(cmd == a or cmd.startswith(a + " ") for a in self.verify_allowlist)

    def approve_write_memory(self, rel_path: str) -> bool:
        # memory 写入始终允许（路径由 memory 模块限制）
        return True

