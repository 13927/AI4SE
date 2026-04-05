from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def now_ms() -> int:
    return int(time.time() * 1000)


def _summarize_tool_payload(name: str, args: Dict[str, Any], result: str | None = None) -> Dict[str, Any]:
    """
    避免日志里塞入大量内容/敏感信息：只保留关键字段与长度。
    """
    safe: Dict[str, Any] = {"tool": name}
    if name in ("write_file",):
        safe["path"] = args.get("path")
        content = args.get("content") or ""
        safe["content_len"] = len(content)
    elif name in ("read_file",):
        safe["path"] = args.get("path")
        safe["max_chars"] = args.get("max_chars")
    elif name.startswith("codewiki_"):
        # codewiki_* 参数一般较小，直接记录
        safe.update(args)
    else:
        # 默认记录 args keys
        safe["args_keys"] = sorted(list(args.keys()))

    if result is not None:
        safe["result_len"] = len(result)
    return safe


@dataclass
class AuditLogger:
    path: Path

    def log(self, event: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("ts_ms", now_ms())
        self.path.write_text("", encoding="utf-8") if not self.path.exists() else None
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def log_user(self, text: str) -> None:
        self.log({"type": "user", "text": text})

    def log_assistant(self, text: str) -> None:
        self.log({"type": "assistant", "text": text})

    def log_decision(self, decision_type: str, allowed: bool, detail: str) -> None:
        self.log({"type": "decision", "decision_type": decision_type, "allowed": allowed, "detail": detail})

    def log_tool(self, name: str, args: Dict[str, Any], result: str | None = None) -> None:
        self.log({"type": "tool", **_summarize_tool_payload(name, args, result=result)})

