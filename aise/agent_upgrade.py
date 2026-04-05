from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UpgradeRequest:
    """
    升级请求对象（MVP）：
    - reason：为什么需要升级
    - add_read_scopes：新增允许读取的 scope（glob 或精确路径）
    - add_write_scopes：新增允许写入的 scope（glob 或精确路径）
    - budget_overrides：可选，提高预算（read/write/verify/tool_calls）
    """

    reason: str
    add_read_scopes: list[str] = field(default_factory=list)
    add_write_scopes: list[str] = field(default_factory=list)
    budget_overrides: dict[str, int] = field(default_factory=dict)


def validate_upgrade_obj(obj: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(obj, dict):
        return False, "upgrade 必须是 JSON 对象"
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        return False, "upgrade.reason 必须是非空字符串"
    for k in ("add_read_scopes", "add_write_scopes"):
        v = obj.get(k, [])
        if v is None:
            continue
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return False, f"upgrade.{k} 必须是字符串数组"
    bo = obj.get("budget_overrides", {})
    if bo is None:
        return True, "ok"
    if not isinstance(bo, dict):
        return False, "upgrade.budget_overrides 必须是对象"
    for k, v in bo.items():
        if k not in ("max_tool_calls", "max_read_calls", "max_write_calls", "max_verify_calls"):
            return False, f"upgrade.budget_overrides 不支持字段：{k}"
        if not isinstance(v, int) or v <= 0:
            return False, f"upgrade.budget_overrides.{k} 必须是正整数"
    return True, "ok"

