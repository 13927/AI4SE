from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

from rich.console import Console
import fnmatch

from rich.prompt import Prompt

from .agent_state import AgentState, Budget, plan_to_pretty_json, validate_plan_obj
from .agent_upgrade import UpgradeRequest, validate_upgrade_obj
from .audit import AuditLogger, now_ms
from .agent_compaction import compact_messages
from .approvals import Approver, InteractiveApprover, touched_files_from_patch
from .config import load_config
from .git_tools import git_apply_patch, git_diff, git_status_porcelain, run as run_cmd
from .memory import ensure_memory_root
from .codewiki_ops import (
    CODEWIKI_DIR,
    init_repo,
    scan_repo,
    validate_l1_static,
    validate_views,
    validation_report,
)
from .llm_openai import OpenAIClient, load_openai_config


console = Console()


@dataclass
class ToolResult:
    content: str


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _codewiki_root(cwd: Path) -> Path:
    return cwd / CODEWIKI_DIR


def _tool_codewiki_get(cwd: Path, id: str, layer: str = "L1") -> ToolResult:
    root = _codewiki_root(cwd)
    if layer not in ("L1", "L2", "views"):
        return ToolResult(content=f"未知 layer={layer}，仅支持 L1/L2/views")

    # L1: module/api/flow/playbook/index
    if layer == "L1":
        if id == "index":
            p = root / "L1/index.json"
            return ToolResult(content=_read_text(p) if p.exists() else "缺少 L1/index.json")
        # 优先 module，其次 api/flow/playbook（便于 Phase 3）
        candidates = [
            (root / "L1/modules" / Path(id + ".json"), f"缺少 L1/modules/{id}.json"),
            (root / "L1/apis" / Path(id + ".json"), f"缺少 L1/apis/{id}.json"),
            (root / "L1/flows" / Path(id + ".json"), f"缺少 L1/flows/{id}.json"),
            (root / "L1/playbooks" / Path(id + ".json"), f"缺少 L1/playbooks/{id}.json"),
        ]
        for p, _msg in candidates:
            if p.exists():
                return ToolResult(content=_read_text(p))
        return ToolResult(content=candidates[0][1])

    if layer == "views":
        p = root / "views" / f"{id}.json"
        return ToolResult(content=_read_text(p) if p.exists() else f"缺少 views/{id}.json")

    # L2 markdown（目前按 module）
    p = root / "L2/modules" / Path(id + ".md")
    return ToolResult(content=_read_text(p) if p.exists() else f"缺少 L2/modules/{id}.md")


def _tool_codewiki_search(cwd: Path, query: str) -> ToolResult:
    """
    极简 search：在 L1/modules 的 responsibility/name/id 中做关键词匹配（不做全文索引）。
    目标：快速跑通 agent 流程，后续再做倒排索引/语义检索。
    """
    root = _codewiki_root(cwd)
    mod_dir = root / "L1/modules"
    if not mod_dir.exists():
        return ToolResult(content="缺少 L1/modules；请先运行 aise scan")

    hits: List[Dict[str, Any]] = []
    for p in mod_dir.rglob("*.json"):
        try:
            data = json.loads(_read_text(p))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        text = " ".join(
            [
                str(data.get("id", "")),
                str(data.get("name", "")),
                str(data.get("responsibility", "")),
            ]
        )
        if query.lower() in text.lower():
            hits.append(
                {
                    "id": data.get("id"),
                    "responsibility": data.get("responsibility"),
                    "path": str(p.relative_to(root)),
                    "confidence": (data.get("provenance", {}) or {}).get("confidence"),
                }
            )

    return ToolResult(content=json.dumps({"query": query, "hits": hits[:20]}, ensure_ascii=False, indent=2))


def _tool_codewiki_validate(cwd: Path) -> ToolResult:
    findings = []
    findings.extend(validate_views(cwd))
    findings.extend(validate_l1_static(cwd))
    rep = validation_report(findings)
    return ToolResult(content=json.dumps(rep, ensure_ascii=False, indent=2))


def _tool_codewiki_scan(cwd: Path) -> ToolResult:
    scan_repo(cwd)
    return ToolResult(content="scan 完成：已生成/更新 docs/codewiki（views + L1/index + L1/modules）。")


def _tool_codewiki_init(cwd: Path, command_name: str) -> ToolResult:
    init_repo(cwd, command_name=command_name)
    return ToolResult(content="init 完成：已确保 git，并创建 docs/codewiki 骨架。")


def _tool_write_file_with_confirm(cwd: Path, path: str, content: str, approver: Approver) -> ToolResult:
    """
    写入需要确认（你选择的权限策略：写入需确认）。
    """
    p = (cwd / path).resolve()
    try:
        p.relative_to(cwd.resolve())
    except Exception:
        return ToolResult(content="拒绝：只允许写入当前仓库目录内的文件。")

    if not approver.approve_write(path):
        return ToolResult(content="用户拒绝写入。")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return ToolResult(content=f"已写入：{path}")


def _is_in_read_scope(rel_path: str, scopes: list[str]) -> bool:
    p = rel_path.replace("\\", "/")
    for s in scopes:
        if fnmatch.fnmatch(p, s):
            return True
    return False


def _is_in_scope(rel_path: str, scopes: list[str]) -> bool:
    return _is_in_read_scope(rel_path, scopes)


def _tool_read_file_with_confirm(
    cwd: Path, path: str, scopes: list[str], max_chars: int = 8000, approver: Approver | None = None
) -> ToolResult:
    """
    读文件（需确认 + 限范围）：
    - 默认只允许读取 scopes 内的路径（glob）
    - 若超出 scopes，可让用户“临时允许该文件（精确路径）”
    - 每次读取都需确认（避免 agent 批量扫仓库）
    """
    repo = cwd.resolve()
    p = (cwd / path).resolve()
    try:
        rel = p.relative_to(repo).as_posix()
    except Exception:
        return ToolResult(content="拒绝：只允许读取当前仓库目录内的文件。")

    if not _is_in_read_scope(rel, scopes):
        # Phase 3：不允许在 read_file 里“临时放行”，必须走 request_upgrade（可审计、可预算）
        return ToolResult(
            content=(
                f"拒绝：超出读范围（read_scope）：{rel}\n"
                "请先调用 request_upgrade 提交升级请求（add_read_scopes 或精确文件路径），说明原因与最小范围。"
            )
        )

    approver = approver or InteractiveApprover()
    if not approver.approve_read(rel):
        return ToolResult(content="用户拒绝读取。")

    if not p.exists() or not p.is_file():
        return ToolResult(content=f"文件不存在：{rel}")

    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return ToolResult(content=text[:max_chars] + "\n\n[TRUNCATED]")
    return ToolResult(content=text)


def _tool_submit_plan_with_confirm(state: AgentState, plan: dict[str, Any], approver: Approver) -> ToolResult:
    ok, msg = validate_plan_obj(plan)
    if not ok:
        return ToolResult(content=f"plan 校验失败：{msg}")

    state.plan = plan
    state.plan_confirmed = False

    pretty = plan_to_pretty_json(plan)
    if approver.approve_plan(pretty):
        state.plan_confirmed = True
        # reset budgets for this task
        state.budget.tool_calls = 0
        state.budget.read_calls = 0
        state.budget.write_calls = 0
        return ToolResult(content="plan 已批准，可以开始执行。")
    return ToolResult(content="plan 未批准，请修改计划后重提。")


def _budget_guard(state: AgentState, kind: str) -> tuple[bool, str]:
    """
    预算闸门（MVP）：
    - tool_calls/read_calls/write_calls 超限即拒绝
    """
    if state.budget.tool_calls >= state.budget.max_tool_calls:
        return False, f"预算超限：tool_calls {state.budget.tool_calls}/{state.budget.max_tool_calls}"
    if kind == "read" and state.budget.read_calls >= state.budget.max_read_calls:
        return False, f"预算超限：read_calls {state.budget.read_calls}/{state.budget.max_read_calls}"
    if kind == "write" and state.budget.write_calls >= state.budget.max_write_calls:
        return False, f"预算超限：write_calls {state.budget.write_calls}/{state.budget.max_write_calls}"
    if kind == "verify" and state.budget.verify_calls >= state.budget.max_verify_calls:
        return False, f"预算超限：verify_calls {state.budget.verify_calls}/{state.budget.max_verify_calls}"
    return True, "ok"


# 暴露给 agent_runner 复用（保持兼容）
_tool_budget_guard = _budget_guard


def build_tools_schema() -> List[Dict[str, Any]]:
    """
    OpenAI tools/function calling schema。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "codewiki_get",
                "description": "读取 CodeWiki 内容（默认 L1）。优先使用它而不是读源码。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string", "description": "moduleId 或特殊值 index / views 文件名"},
                        "layer": {"type": "string", "enum": ["L1", "L2", "views"], "default": "L1"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codewiki_search",
                "description": "在 L1/modules 中按关键词检索模块（MVP：非全文检索）。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codewiki_validate",
                "description": "运行静态校验（views + L1 核心规则），返回报告 JSON。",
                "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "codewiki_scan",
                "description": "执行 aise scan（生成/更新 views 与 L1）。",
                "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": "提交执行计划（Plan Contract）。在执行 read_file/write_file/codewiki_scan 等操作前必须先提交并获得用户批准。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["plan"],
                    "properties": {
                        "plan": {
                            "type": "object",
                            "description": "计划对象：goal/success_criteria/wiki_reads/need_deep_read/deep_read_files/writes/verifications",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "request_upgrade",
                "description": "请求升级权限/范围（Phase 3）。用于扩大 read/write scope 或提高预算，必须给出理由与最小范围。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["upgrade"],
                    "properties": {
                        "upgrade": {
                            "type": "object",
                            "description": "升级对象：reason/add_read_scopes/add_write_scopes/budget_overrides",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "获取 git status（只读）。",
                "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": "获取 git diff（只读，默认 HEAD）。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "传给 `git diff` 的参数数组（例如 [\"--stat\"], [\"HEAD~1..HEAD\"]）。",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_patch",
                "description": "提出 unified diff patch（不应用）。建议用于 patch-first 工作流。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["patch"],
                    "properties": {"patch": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_apply_patch",
                "description": "应用 unified diff patch（需要用户确认）。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["patch"],
                    "properties": {"patch": {"type": "string"}, "check": {"type": "boolean", "default": True}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_verify",
                "description": "执行验证命令（白名单 + 需要用户确认）。用于 mvn test/ctest/pytest 等。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["command"],
                    "properties": {"command": {"type": "string"}, "timeout_s": {"type": "integer", "default": 600}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_memory",
                "description": "读取 .aise/memory/ 下的记忆文件（project_policy.md/user_preferences.md/ongoing_tasks.md）。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_memory",
                "description": "写入 .aise/memory/ 下的记忆文件（需用户确认）。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "content"],
                    "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取源码文件（需用户确认 + 限范围）。默认只允许读取 docs/codewiki/**；必要时可临时允许单个文件。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 8000}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写文件（需要用户确认）。用于更新 CodeWiki 或生成补充文档。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "content"],
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                },
            },
        },
    ]


def run_agent_repl(
    command_name: str = "app",
    read_scopes: list[str] | None = None,
    *,
    max_tool_calls: int = 20,
    max_read_calls: int = 8,
    max_write_calls: int = 6,
    max_verify_calls: int = 3,
    compact_threshold_messages: int = 60,
) -> None:
    """
    Claude Code 风格的最小 REPL（原型）：
    - wiki-first：优先读 docs/codewiki（L1 结构化）
    - Plan Contract：执行前必须 submit_plan 并获得批准
    - deep-read / write：需确认 + 预算限制
    - 使用 OpenAI 兼容 tools/function calling
    """
    cwd = Path.cwd()
    cfg = load_openai_config()
    client = OpenAIClient(cfg)

    console.print("[bold green]aise agent REPL[/bold green]（wiki-first，Plan Contract，读写需确认）")
    console.print("提示：第一次运行建议先执行 aise init + aise scan。")

    # 确保 codewiki 存在（不强制 scan，避免误覆盖）
    if not (_codewiki_root(cwd) / "views/filetree.json").exists():
        approver0: Approver = InteractiveApprover()
        # 交互模式下保持原行为：提示用户批准 init+scan
        if approver0.approve_plan("检测到 docs/codewiki 不存在，是否先执行 init + scan？"):
            console.print(_tool_codewiki_init(cwd, command_name).content)
            console.print(_tool_codewiki_scan(cwd).content)

    system = (
        "你是 aise，一个 coding agent。必须优先使用 CodeWiki（L1 结构化）来定位与约束行为。\n"
        "规则：\n"
        "1) 先 codewiki_validate，确保 wiki 可信。\n"
        "2) 先 codewiki_search/ codewiki_get 获取必要模块约束；不要直接假设。\n"
        "3) 不要把大量文档全文塞进上下文，只取必要片段。\n"
        "4) 执行任何 read_file/write_file/codewiki_scan 前，必须先 submit_plan 并获得用户批准（Plan Contract）。\n"
        "5) 任何读写都需要用户确认，且受预算限制；超限必须缩小范围或重新提 plan。\n"
        "6) patch-first：优先提出 patch（propose_patch），经用户批准后再 git_apply_patch。\n"
        "7) 验证：仅允许执行白名单 verifyAllowlist 中的命令（run_verify），且需用户确认。\n"
        "8) 升级路径：当需要读源码/扩大范围/提高预算时，必须先 request_upgrade（最小范围 + 理由），经用户批准后才能继续。\n"
        "9) memory：可用 read_memory/write_memory 管理长期记忆（需确认写入）。\n"
        "10) 若缺少信息，提出需要补齐的 L1 字段或建议运行 codewiki_scan。\n"
    )

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    tools = build_tools_schema()
    # policy-as-code：read/write scopes 默认来自 aise.yml；CLI 参数优先
    state.read_scopes = read_scopes[:] if read_scopes is not None else list(cfg.read_scopes)
    state.write_scopes = list(cfg.write_scopes)

    state = AgentState(
        budget=Budget(
            max_tool_calls=max_tool_calls,
            max_read_calls=max_read_calls,
            max_write_calls=max_write_calls,
            max_verify_calls=max_verify_calls,
        )
    )
    session_id = str(now_ms())
    audit = AuditLogger(path=(cwd / ".aise/logs" / f"session-{session_id}.jsonl"))
    cfg = load_config(cwd)
    mem = ensure_memory_root(cwd)
    approver: Approver = InteractiveApprover()

    while True:
        user = Prompt.ask("[bold]user[/bold]", default="")
        if user.strip() in ("/exit", "exit", "quit", ":q"):
            break
        if not user.strip():
            continue

        messages.append({"role": "user", "content": user})
        audit.log_user(user)

        # 每轮最多做 N 次工具调用，防止死循环
        for _step in range(12):
            # compaction：消息过长时先压缩（Phase 3 v1）
            if len(messages) > compact_threshold_messages:
                messages, summary = compact_messages(client=client, messages=messages, max_keep=24)
                audit.log({"type": "compact", "summary_len": len(summary), "kept_messages": 24})

            resp = client.chat_completions(messages=messages, tools=tools, tool_choice="auto")
            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message") or {}

            # tool calls?
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                # 先把 assistant message 放进去（包含 tool_calls）
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )

                for tc in tool_calls:
                    fn = (tc.get("function") or {}).get("name")
                    args_raw = (tc.get("function") or {}).get("arguments") or "{}"
                    try:
                        args = json.loads(args_raw)
                    except Exception:
                        args = {}

                    state.budget.tool_calls += 1

                    # 未批准 plan 前，只允许只读 codewiki_* 与 submit_plan
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
                        out = _tool_codewiki_get(cwd, id=args.get("id", ""), layer=args.get("layer", "L1"))
                    elif fn == "codewiki_search":
                        out = _tool_codewiki_search(cwd, query=args.get("query", ""))
                    elif fn == "codewiki_validate":
                        out = _tool_codewiki_validate(cwd)
                    elif fn == "codewiki_scan":
                        ok, msg2 = _budget_guard(state, kind="write")
                        if not ok:
                            out = ToolResult(content=f"拒绝：{msg2}")
                        else:
                            state.budget.write_calls += 1
                            out = _tool_codewiki_scan(cwd)
                    elif fn == "submit_plan":
                        plan = args.get("plan")
                        if not isinstance(plan, dict):
                            out = ToolResult(content="submit_plan 参数错误：plan 必须是 JSON 对象")
                        else:
                            out = _tool_submit_plan_with_confirm(state, plan)
                    elif fn == "request_upgrade":
                        upgrade = args.get("upgrade")
                        if not isinstance(upgrade, dict):
                            out = ToolResult(content="request_upgrade 参数错误：upgrade 必须是 JSON 对象")
                        else:
                            ok, msg3 = validate_upgrade_obj(upgrade)
                            if not ok:
                                out = ToolResult(content=f"upgrade 校验失败：{msg3}")
                            else:
                                pretty = json.dumps(upgrade, ensure_ascii=False, indent=2)
                                if approver.approve_upgrade(pretty):
                                    ur = UpgradeRequest(
                                        reason=str(upgrade.get("reason")),
                                        add_read_scopes=[str(x) for x in (upgrade.get("add_read_scopes") or [])],
                                        add_write_scopes=[str(x) for x in (upgrade.get("add_write_scopes") or [])],
                                        budget_overrides={str(k): int(v) for k, v in (upgrade.get("budget_overrides") or {}).items()},
                                    )
                                    state.upgrades.append(ur)
                                    # apply scopes
                                    for s in ur.add_read_scopes:
                                        if s not in state.read_scopes:
                                            state.read_scopes.append(s)
                                    for s in ur.add_write_scopes:
                                        if s not in state.write_scopes:
                                            state.write_scopes.append(s)
                                    # apply budget overrides
                                    for k, v in ur.budget_overrides.items():
                                        if hasattr(state.budget, k):
                                            setattr(state.budget, k, v)
                                    out = ToolResult(content="升级请求已批准并生效。")
                                else:
                                    out = ToolResult(content="升级请求未批准。")
                    elif fn == "git_status":
                        out = ToolResult(content=git_status_porcelain(cwd))
                    elif fn == "git_diff":
                        diff_args = args.get("args") or []
                        if not isinstance(diff_args, list):
                            diff_args = []
                        out = ToolResult(content=git_diff(cwd, [str(x) for x in diff_args]))
                    elif fn == "propose_patch":
                        patch = args.get("patch") or ""
                        out = ToolResult(content=patch)
                    elif fn == "git_apply_patch":
                        ok, msg2 = _budget_guard(state, kind="write")
                        if not ok:
                            out = ToolResult(content=f"拒绝：{msg2}")
                        else:
                            patch = args.get("patch") or ""
                            check = bool(args.get("check", True))
                            touched = touched_files_from_patch(patch)
                            # policy-as-code：写入 scope 限制（必须走 request_upgrade 才能扩）
                            if touched and not all(_is_in_scope(p, state.write_scopes) for p in touched):
                                out = ToolResult(
                                    content=(
                                        "拒绝：patch 触及超出 writeScopes 的路径。\n"
                                        "请先 request_upgrade（add_write_scopes）并说明原因与最小范围。"
                                    )
                                )
                            elif not approver.approve_apply_patch(touched):
                                out = ToolResult(content="用户拒绝应用 patch。")
                            else:
                                try:
                                    git_apply_patch(cwd, patch_text=patch, check=check)
                                    state.budget.write_calls += 1
                                    out = ToolResult(content="patch 已应用（git apply）。")
                                except Exception as e:
                                    out = ToolResult(content=f"git_apply_patch 失败：{e}")
                    elif fn == "run_verify":
                        ok, msg2 = _budget_guard(state, kind="verify")
                        if not ok:
                            out = ToolResult(content=f"拒绝：{msg2}")
                        else:
                            cmd = str(args.get("command") or "").strip()
                            timeout_s = int(args.get("timeout_s", 600) or 600)
                            if not cmd:
                                out = ToolResult(content="run_verify 参数错误：command 不能为空")
                            else:
                                # allowlist：前缀匹配（更可控）
                                allowed = any(cmd == a or cmd.startswith(a + " ") for a in cfg.verify_allowlist)
                                if not allowed:
                                    out = ToolResult(content=f"拒绝：命令不在白名单 verifyAllowlist 中：{cmd}")
                                elif not approver.approve_verify(cmd):
                                    out = ToolResult(content="用户拒绝执行验证命令。")
                                else:
                                    r = run_cmd(["bash", "-lc", cmd], cwd, timeout_s=timeout_s)
                                    state.budget.verify_calls += 1
                                    out = ToolResult(content=f"exit={r.code}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
                    elif fn == "write_file":
                        ok, msg2 = _budget_guard(state, kind="write")
                        if not ok:
                            out = ToolResult(content=f"拒绝：{msg2}")
                        else:
                            relp = str(args.get("path", "") or "").replace("\\", "/")
                            if relp and not _is_in_scope(relp, state.write_scopes):
                                out = ToolResult(
                                    content=(
                                        f"拒绝：写入路径超出 writeScopes：{relp}\n"
                                        "请先 request_upgrade（add_write_scopes）并说明原因与最小范围。"
                                    )
                                )
                            else:
                                state.budget.write_calls += 1
                                out = _tool_write_file_with_confirm(
                                    cwd, path=str(args.get("path", "") or ""), content=str(args.get("content", "") or ""), approver=approver
                                )
                    elif fn == "read_file":
                        ok, msg2 = _budget_guard(state, kind="read")
                        if not ok:
                            out = ToolResult(content=f"拒绝：{msg2}")
                        else:
                            state.budget.read_calls += 1
                            out = _tool_read_file_with_confirm(
                                cwd,
                                path=args.get("path", ""),
                                scopes=state.read_scopes,
                                max_chars=int(args.get("max_chars", 8000) or 8000),
                                approver=approver,
                            )
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
                            if not approver.approve_write_memory(str(p.relative_to(cwd))):
                                out = ToolResult(content="用户拒绝写入 memory。")
                            else:
                                p.write_text(content2, encoding="utf-8")
                                out = ToolResult(content="memory 已写入。")
                        except Exception as e:
                            out = ToolResult(content=f"write_memory 失败：{e}")
                    else:
                        out = ToolResult(content=f"未知工具：{fn}")

                    audit.log_tool(fn or "unknown", args, result=out.content)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": out.content,
                        }
                    )
                continue

            # no tool call, final assistant response
            content = msg.get("content") or ""
            messages.append({"role": "assistant", "content": content})
            audit.log_assistant(content)
            console.print("\n[bold cyan]assistant[/bold cyan]")
            console.print(content)
            console.print()
            break
