from __future__ import annotations

from aise.agent_state import validate_plan_obj


def test_validate_plan_ok():
    ok, msg = validate_plan_obj(
        {
            "goal": "x",
            "success_criteria": ["a"],
            "wiki_reads": ["index", "core"],
            "need_deep_read": False,
            "deep_read_files": [],
            "writes": [],
            "verifications": ["aise validate --mode static"],
        }
    )
    assert ok, msg


def test_validate_plan_missing_key():
    ok, msg = validate_plan_obj({"goal": "x"})
    assert not ok
    assert "缺少字段" in msg

