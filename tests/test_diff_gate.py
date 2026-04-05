from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aise.codewiki_ops import init_repo, scan_repo, validate_l1_diff


def _git(cwd: Path, *args: str) -> None:
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    assert p.returncode == 0, p.stderr


def test_diff_gate_allowlist_warn(tmp_path: Path):
    # init git
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.txt").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", "src/a.txt")
    _git(tmp_path, "commit", "-m", "init")

    # init + scan codewiki
    init_repo(tmp_path, command_name="app")
    scan_repo(tmp_path)

    # add allow_write_path to core
    core = tmp_path / "docs/codewiki/L1/modules/core.json"
    data = json.loads(core.read_text(encoding="utf-8"))
    data["boundaries"].append(
        {
            "type": "must",
            "statement": "只允许写入 docs/codewiki/**",
            "machine": {"kind": "allow_write_path", "targets": ["docs/codewiki/**"]},
        }
    )
    core.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/codewiki/L1/modules/core.json")
    _git(tmp_path, "commit", "-m", "gate")

    # make a change outside allowlist
    (tmp_path / "other").mkdir()
    (tmp_path / "other/out.txt").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "other/out.txt")
    _git(tmp_path, "commit", "-m", "out")

    findings = validate_l1_diff(tmp_path, base="HEAD~1", head="HEAD")
    # 默认 strict_diff_gate=false，应为 warn
    assert any(f.rule_id == "R-DIFF-002" and f.severity == "warn" for f in findings)

