from __future__ import annotations

from aise.agent_upgrade import validate_upgrade_obj


def test_validate_upgrade_ok():
    ok, msg = validate_upgrade_obj(
        {
            "reason": "需要读取一个文件来确认入口",
            "add_read_scopes": ["src/main/java/**"],
            "add_write_scopes": [],
            "budget_overrides": {"max_read_calls": 20},
        }
    )
    assert ok, msg


def test_validate_upgrade_reject_unknown_budget_key():
    ok, msg = validate_upgrade_obj({"reason": "x", "budget_overrides": {"hack": 1}})
    assert not ok
    assert "不支持字段" in msg

