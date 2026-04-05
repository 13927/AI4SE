from __future__ import annotations

from pathlib import Path

import pytest

from aise.memory import ensure_memory_root


def test_memory_root_creates_files(tmp_path: Path):
    mem = ensure_memory_root(tmp_path)
    assert (mem.root / "project_policy.md").exists()
    assert (mem.root / "user_preferences.md").exists()
    assert (mem.root / "ongoing_tasks.md").exists()


def test_memory_rejects_unknown_name(tmp_path: Path):
    mem = ensure_memory_root(tmp_path)
    with pytest.raises(ValueError):
        mem.file_path("hack.txt")

