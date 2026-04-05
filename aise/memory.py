from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ALLOWED_MEMORY_FILES = {
    "project_policy.md",
    "user_preferences.md",
    "ongoing_tasks.md",
}


@dataclass(frozen=True)
class MemoryPaths:
    root: Path  # repo/.aise/memory

    def file_path(self, name: str) -> Path:
        if name not in ALLOWED_MEMORY_FILES:
            raise ValueError(f"不允许的 memory 文件名：{name}，允许：{sorted(ALLOWED_MEMORY_FILES)}")
        return self.root / name


def ensure_memory_root(repo_root: Path) -> MemoryPaths:
    root = repo_root / ".aise/memory"
    root.mkdir(parents=True, exist_ok=True)
    # 预创建空文件（不覆盖）
    for n in sorted(ALLOWED_MEMORY_FILES):
        p = root / n
        if not p.exists():
            p.write_text("", encoding="utf-8")
    return MemoryPaths(root=root)

