from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .agent_state import AgentState, Budget
from .approvals import NonInteractiveApprover, touched_files_from_patch
from .audit import AuditLogger, now_ms
from .config import load_config
from .llm_openai import OpenAIClient, load_openai_config
from .memory import ensure_memory_root

# 复用 agent_runtime 的工具实现（避免重复逻辑）
from .agent_runtime import (  # noqa: WPS433 - internal reuse is fine here
    ToolResult,
    _tool_budget_guard,
    _tool_codewiki_get,
    _tool_codewiki_search,
    _tool_codewiki_scan,
    _tool_codewiki_validate,
    _tool_read_file_with_confirm,
    _tool_submit_plan_with_confirm,
    _tool_write_file_with_confirm,
    build_tools_schema,
    _is_in_scope,
)
from .agent_upgrade import UpgradeRequest, validate_upgrade_obj
from .git_tools import git_apply_patch, git_diff, git_status_porcelain, run as run_cmd


@dataclass(frozen=True)
class Scenario:
    task: str
    allow_upgrade: bool = False
    allowed_upgrade_read_scopes: list[str] | None = None
    allowed_upgrade_write_scopes: list[str] | None = None


def load_scenario(path: Path) -> Scenario:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("task"), str):
        raise ValueError("scenario 必须是 JSON 对象且包含 task 字符串")
    appr = raw.get("approvals") or {}
    if not isinstance(appr, dict):
        appr = {}
    return Scenario(
        task=str(raw["task"]),
        allow_upgrade=bool(appr.get("allow_upgrade", False)),
        allowed_upgrade_read_scopes=(appr.get("allowed_upgrade_read_scopes") or None),
        allowed_upgrade_write_scopes=(appr.get("allowed_upgrade_write_scopes") or None),
    )


def run_agent_noninteractive(
    *,
    repo_root: Path,
    task: str,
    max_steps: int = 80,
    scenario: Scenario | None = None,
) -> int:
    """
    非交互式 agent runner：
    - 用 NonInteractiveApprover 自动审批（policy 驱动）
    - 输出审计日志到 .aise/logs/
    - 返回退出码（0=成功，非 0=失败）
    """
    repo_root = repo_root.resolve()
    cfg = load_config(repo_root)
    state = AgentState(
        budget=Budget(**cfg.agent_budgets),
    )
    state.read_scopes = list(cfg.read_scopes)
    state.write_scopes = list(cfg.write_scopes)

    approver = NonInteractiveApprover(
        read_scopes=state.read_scopes,
        write_scopes=state.write_scopes,
        verify_allowlist=cfg.verify_allowlist,
    )

    mem = ensure_memory_root(repo_root)

    session_id = str(now_ms())
    audit = AuditLogger(path=(repo_root / ".aise/logs" / f"session-{session_id}.jsonl"))

    client = OpenAIClient(load_openai_config())

    system = (
        "你是 aise，一个 coding agent。当前处于非交互执行模式（CI）。\n"
        "必须遵守：Plan Contract、patch-first、verifyAllowlist、readScopes/writeScopes。\n"
        "若需要扩大范围，必须 request_upgrade；若升级未被允许，则应给出替代方案。\n"
    )
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    audit.log_user(task)
    tools = build_tools_schema()

    last_assistant = ""
    for _ in range(max_steps):
        resp = client.chat_completions(messages=messages, tools=tools, tool_choice="auto")
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = (tc.get("function") or {}).get("name")
                args_raw = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}

                state.budget.tool_calls += 1

                # 未批准 plan 前，只允许只读 codewiki_* 与 submit_plan/request_upgrade
                if not state.plan_confirmed and fn not in (
                    "submit_plan",
                    "request_upgrade",
                    "codewiki_get",
                    "codewiki_search",
                    "codewiki_validate",
                ):
                    out = ToolResult(content="拒绝：未提交并批准 plan（Plan Contract）。请先调用 submit_plan。")
                    audit.log_tool(fn or "unknown", args, result=out.content)
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out.content})
                    continue

                if fn == "codewiki_get":
                    out = _tool_codewiki_get(repo_root, id=args.get("id", ""), layer=args.get("layer", "L1"))
                elif fn == "codewiki_search":
                    out = _tool_codewiki_search(repo_root, query=args.get("query", ""))
                elif fn == "codewiki_validate":
                    out = _tool_codewiki_validate(repo_root)
                elif fn == "codewiki_scan":
                    ok, msg2 = _tool_budget_guard(state, kind="write")
                    if not ok:
                        out = ToolResult(content=f"拒绝：{msg2}")
                    else:
                        state.budget.write_calls += 1
                        out = _tool_codewiki_scan(repo_root)
                elif fn == "submit_plan":
                    plan = args.get("plan")
                    if not isinstance(plan, dict):
                        out = ToolResult(content="submit_plan 参数错误：plan 必须是 JSON 对象")
                    else:
                        out = _tool_submit_plan_with_confirm(state, plan, approver)
                elif fn == "request_upgrade":
                    upgrade = args.get("upgrade")
                    if not isinstance(upgrade, dict):
                        out = ToolResult(content="request_upgrade 参数错误：upgrade 必须是 JSON 对象")
                    else:
                        ok, msg3 = validate_upgrade_obj(upgrade)
                        if not ok:
                            out = ToolResult(content=f"upgrade 校验失败：{msg3}")
                        elif scenario and (not scenario.allow_upgrade):
                            out = ToolResult(content="拒绝：scenario 禁止升级（allow_upgrade=false）。")
                        else:
                            # 额外限制：只允许升级到 scenario 允许的 scopes（若提供）
                            add_r = [str(x) for x in (upgrade.get("add_read_scopes") or [])]
                            add_w = [str(x) for x in (upgrade.get("add_write_scopes") or [])]
                            if scenario and scenario.allowed_upgrade_read_scopes:
                                if not all(_is_in_scope(s, scenario.allowed_upgrade_read_scopes) for s in add_r):
                                    out = ToolResult(content="拒绝：升级 read scope 超出 scenario.allowed_upgrade_read_scopes")
                                    audit.log_tool(fn or "unknown", args, result=out.content)
                                    messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out.content})
                                    continue
                            if scenario and scenario.allowed_upgrade_write_scopes:
                                if not all(_is_in_scope(s, scenario.allowed_upgrade_write_scopes) for s in add_w):
                                    out = ToolResult(content="拒绝：升级 write scope 超出 scenario.allowed_upgrade_write_scopes")
                                    audit.log_tool(fn or "unknown", args, result=out.content)
                                    messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out.content})
                                    continue
                            # 自动批准并生效
                            ur = UpgradeRequest(
                                reason=str(upgrade.get("reason")),
                                add_read_scopes=add_r,
                                add_write_scopes=add_w,
                                budget_overrides={str(k): int(v) for k, v in (upgrade.get("budget_overrides") or {}).items()},
                            )
                            state.upgrades.append(ur)
                            for s in ur.add_read_scopes:
                                if s not in state.read_scopes:
                                    state.read_scopes.append(s)
                            for s in ur.add_write_scopes:
                                if s not in state.write_scopes:
                                    state.write_scopes.append(s)
                            for k, v in ur.budget_overrides.items():
                                if hasattr(state.budget, k):
                                    setattr(state.budget, k, v)
                            out = ToolResult(content="升级请求已批准并生效。")
                elif fn == "git_status":
                    out = ToolResult(content=git_status_porcelain(repo_root))
                elif fn == "git_diff":
                    diff_args = args.get("args") or []
                    if not isinstance(diff_args, list):
                        diff_args = []
                    out = ToolResult(content=git_diff(repo_root, [str(x) for x in diff_args]))
                elif fn == "propose_patch":
                    out = ToolResult(content=str(args.get("patch") or ""))
                elif fn == "git_apply_patch":
                    ok, msg2 = _tool_budget_guard(state, kind="write")
                    if not ok:
                        out = ToolResult(content=f"拒绝：{msg2}")
                    else:
                        patch = str(args.get("patch") or "")
                        touched = touched_files_from_patch(patch)
                        if touched and not approver.approve_apply_patch(touched):
                            out = ToolResult(content="拒绝：非交互审批不允许对 writeScopes 之外文件应用 patch。")
                        else:
                            try:
                                git_apply_patch(repo_root, patch_text=patch, check=bool(args.get("check", True)))
                                state.budget.write_calls += 1
                                out = ToolResult(content="patch 已应用（git apply）。")
                            except Exception as e:
                                out = ToolResult(content=f"git_apply_patch 失败：{e}")
                elif fn == "run_verify":
                    ok, msg2 = _tool_budget_guard(state, kind="verify")
                    if not ok:
                        out = ToolResult(content=f"拒绝：{msg2}")
                    else:
                        cmd = str(args.get("command") or "").strip()
                        timeout_s = int(args.get("timeout_s", 600) or 600)
                        if not cmd:
                            out = ToolResult(content="run_verify 参数错误：command 不能为空")
                        elif not approver.approve_verify(cmd):
                            out = ToolResult(content=f"拒绝：命令不在 verifyAllowlist：{cmd}")
                        else:
                            r = run_cmd(["bash", "-lc", cmd], repo_root, timeout_s=timeout_s)
                            state.budget.verify_calls += 1
                            out = ToolResult(content=f"exit={r.code}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
                elif fn == "read_file":
                    ok, msg2 = _tool_budget_guard(state, kind="read")
                    if not ok:
                        out = ToolResult(content=f"拒绝：{msg2}")
                    else:
                        state.budget.read_calls += 1
                        out = _tool_read_file_with_confirm(
                            repo_root,
                            path=str(args.get("path") or ""),
                            scopes=state.read_scopes,
                            max_chars=int(args.get("max_chars", 8000) or 8000),
                            approver=approver,
                        )
                elif fn == "write_file":
                    ok, msg2 = _tool_budget_guard(state, kind="write")
                    if not ok:
                        out = ToolResult(content=f"拒绝：{msg2}")
                    else:
                        relp = str(args.get("path") or "").replace("\\", "/")
                        if relp and not _is_in_scope(relp, state.write_scopes):
                            out = ToolResult(content=f"拒绝：写入路径超出 writeScopes：{relp}")
                        elif not approver.approve_write(relp):
                            out = ToolResult(content="拒绝：非交互审批拒绝写入。")
                        else:
                            state.budget.write_calls += 1
                            out = _tool_write_file_with_confirm(repo_root, path=relp, content=str(args.get("content") or ""), approver=approver)
                elif fn == "read_memory":
                    name = str(args.get("name") or "")
                    try:
                        p = mem.file_path(name)
                        out = ToolResult(content=p.read_text(encoding="utf-8", errors="replace"))
                    except Exception as e:
                        out = ToolResult(content=f"read_memory 失败：{e}")
                elif fn == "write_memory":
                    name = str(args.get("name") or "")
                    content2 = str(args.get("content") or "")
                    try:
                        p = mem.file_path(name)
                        p.write_text(content2, encoding="utf-8")
                        out = ToolResult(content="memory 已写入。")
                    except Exception as e:
                        out = ToolResult(content=f"write_memory 失败：{e}")
                else:
                    out = ToolResult(content=f"未知工具：{fn}")

                audit.log_tool(fn or "unknown", args, result=out.content)
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out.content})
            continue

        # final assistant response
        last_assistant = str(msg.get("content") or "")
        messages.append({"role": "assistant", "content": last_assistant})
        audit.log_assistant(last_assistant)
        break

    # 成功判定：只要没有明确 error，返回 0；更严格断言交给 CI 场景脚本
    return 0 if last_assistant else 2

