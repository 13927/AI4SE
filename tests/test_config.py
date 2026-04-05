from __future__ import annotations

from pathlib import Path

from aise.config import load_config


def test_load_config_defaults(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.roots
    assert "src" in cfg.roots[0]


def test_load_config_from_yaml(tmp_path: Path):
    (tmp_path / "aise.yml").write_text(
        "roots:\n  - app\nignore:\n  - build/**\nstrictDiffGate: true\nreadScopes:\n  - docs/codewiki/**\nwriteScopes:\n  - docs/codewiki/**\nagentBudgets:\n  max_read_calls: 9\n",
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()
    cfg = load_config(tmp_path)
    assert cfg.roots == ["app"]
    assert cfg.ignore == ["build/**"]
    assert cfg.strict_diff_gate is True
    assert cfg.read_scopes == ["docs/codewiki/**"]
    assert cfg.agent_budgets["max_read_calls"] == 9
