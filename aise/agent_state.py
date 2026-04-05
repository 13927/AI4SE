from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .agent_upgrade import UpgradeRequest

REQUIRED_PLAN_KEYS = [
    "goal",
    "success_criteria",
    "wiki_reads",
    "need_deep_read",
    "deep_read_files",
    "writes",
    "verifications",
]


def validate_plan_obj(plan: dict[str, Any]) -> tuple[bool, str]:
    """
    Plan Contract（MVP）：
    - plan 必须是 dict，并包含 REQUIRED_PLAN_KEYS
    - deep_read_files / wiki_reads / writes / verifications 必须是 list
    - need_deep_read 必须是 bool
    """
    if not isinstance(plan, dict):
        return False, "plan 必须是 JSON 对象（dict）"
    missing = [k for k in REQUIRED_PLAN_KEYS if k not in plan]
    if missing:
        return False, f"plan 缺少字段：{', '.join(missing)}"
    for k in ("wiki_reads", "deep_read_files", "writes", "verifications"):
        if not isinstance(plan.get(k), list):
            return False, f"plan.{k} 必须是数组（list）"
    if not isinstance(plan.get("need_deep_read"), bool):
        return False, "plan.need_deep_read 必须是布尔值（true/false）"
    if not isinstance(plan.get("goal"), str) or not plan["goal"].strip():
        return False, "plan.goal 必须是非空字符串"
    if not isinstance(plan.get("success_criteria"), list) or not plan["success_criteria"]:
        return False, "plan.success_criteria 必须是非空数组"
    return True, "ok"


def plan_to_pretty_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)


@dataclass
class Budget:
    max_tool_calls: int = 20
    max_read_calls: int = 8
    max_write_calls: int = 6
    max_verify_calls: int = 3

    tool_calls: int = 0
    read_calls: int = 0
    write_calls: int = 0
    verify_calls: int = 0


@dataclass
class AgentState:
    plan: dict[str, Any] | None = None
    plan_confirmed: bool = False
    budget: Budget = field(default_factory=Budget)
    read_scopes: list[str] = field(default_factory=lambda: ["docs/codewiki/**"])
    write_scopes: list[str] = field(default_factory=lambda: ["docs/codewiki/**"])
    upgrades: list[UpgradeRequest] = field(default_factory=list)
