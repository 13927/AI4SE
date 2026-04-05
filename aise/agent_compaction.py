from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol


class ChatClient(Protocol):
    def chat_completions(self, messages: List[Dict[str, Any]], tools=None, tool_choice=None) -> Dict[str, Any]: ...


SUMMARY_PREFIX = "【会话摘要】\n"


def _truncate(s: str, n: int = 1200) -> str:
    return s if len(s) <= n else s[:n] + "\n[TRUNCATED]"


def build_compaction_prompt() -> str:
    """
    v1：结构化摘要契约（参考 Claude Code compact prompt 的字段取向）。
    """
    return (
        "你是一个会话压缩器。请将对话压缩成一段结构化中文摘要，要求：\n"
        "1) 必须保留：用户目标、当前进度、关键决策（批准/拒绝/升级）、读写过的文件路径、生成的主要产物、下一步动作。\n"
        "2) 必须简洁，不要抄原文。\n"
        "3) 输出格式：\n"
        "【会话摘要】\n"
        "- 目标：...\n"
        "- 当前进度：...\n"
        "- 已批准的计划/升级：...\n"
        "- 读过的关键文件：...\n"
        "- 写入/生成的关键文件：...\n"
        "- 风险/约束：...\n"
        "- 下一步：...\n"
    )


def compact_messages(
    *,
    client: ChatClient,
    messages: List[Dict[str, Any]],
    max_keep: int = 24,
) -> tuple[List[Dict[str, Any]], str]:
    """
    将 messages 压缩：
    - 保留 system（第一条）+ summary system（第二条）+ 最后 max_keep 条（不含 system/summary）
    - summary 由模型生成；若失败则降级为简单拼接摘要
    返回：(new_messages, summary_text)
    """
    if not messages or messages[0].get("role") != "system":
        return messages, ""

    # 找到已有 summary（若存在）
    summary_idx = None
    if len(messages) > 1 and messages[1].get("role") == "system" and str(messages[1].get("content", "")).startswith(SUMMARY_PREFIX):
        summary_idx = 1

    tail = messages[1:] if summary_idx is None else messages[2:]
    tail = tail[-max_keep:]

    # 组装压缩输入（避免把巨大 tool 输出塞进去）
    compact_input: list[dict[str, Any]] = []
    for m in tail:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            compact_input.append({"role": "tool", "content": _truncate(str(content or ""), 800)})
        else:
            compact_input.append({"role": role, "content": _truncate(str(content or ""), 1200)})

    prompt = build_compaction_prompt()
    req = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "请压缩下面的会话片段：\n" + json.dumps(compact_input, ensure_ascii=False, indent=2)},
    ]

    summary_text = ""
    try:
        resp = client.chat_completions(messages=req)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        summary_text = str(msg.get("content") or "").strip()
    except Exception:
        # 降级：不调用模型，给一个弱摘要（仍有前缀，避免破坏后续逻辑）
        summary_text = SUMMARY_PREFIX + "- 目标：\n- 当前进度：\n- 下一步：\n"

    if not summary_text.startswith(SUMMARY_PREFIX):
        summary_text = SUMMARY_PREFIX + summary_text

    new_messages = [messages[0]]
    new_messages.append({"role": "system", "content": summary_text})
    new_messages.extend(tail)
    return new_messages, summary_text

