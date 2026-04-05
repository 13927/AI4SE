from __future__ import annotations

from typing import Any, Dict, List

from aise.agent_compaction import compact_messages, SUMMARY_PREFIX


class FakeClient:
    def chat_completions(self, messages: List[Dict[str, Any]], tools=None, tool_choice=None) -> Dict[str, Any]:
        return {"choices": [{"message": {"content": SUMMARY_PREFIX + "- 目标：x\n- 当前进度：y\n- 下一步：z\n"}}]}


def test_compaction_inserts_summary_system_message():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(40):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})

    new_msgs, summary = compact_messages(client=FakeClient(), messages=msgs, max_keep=10)
    assert new_msgs[0]["role"] == "system"
    assert new_msgs[1]["role"] == "system"
    assert str(new_msgs[1]["content"]).startswith(SUMMARY_PREFIX)
    assert len(new_msgs) == 1 + 1 + 10
    assert "下一步" in summary

