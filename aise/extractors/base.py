from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Entrypoint:
    id: str
    type: str  # cli/http_route/worker/cron/event/other
    match_kind: str  # literal/glob/regex
    match_value: str
    summary: str
    modules: list[str]
    source_kind: str = "other"
    source_ref: str = "auto"

    def to_view_obj(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "match": {"kind": self.match_kind, "value": self.match_value},
            "summary": self.summary,
            "targets": {"modules": self.modules},
            "source": {"kind": self.source_kind, "ref": self.source_ref},
        }


@dataclass(frozen=True)
class CMakeTarget:
    name: str
    kind: str  # library|executable|unknown
    sources: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractResult:
    entrypoints: list[Entrypoint] = field(default_factory=list)
    extra_modules: list[str] = field(default_factory=list)
    # module_id -> depends_on module_ids（仅用于“新建模块”时填充，避免覆盖人工编辑）
    module_depends_on: dict[str, list[str]] = field(default_factory=dict)
    # 可选：把构建目标等写到 module 的 “related/notes”，原型阶段先不强求。


def merge_entrypoints(*groups: list[Entrypoint]) -> list[Entrypoint]:
    seen: set[str] = set()
    out: list[Entrypoint] = []
    for g in groups:
        for e in g:
            if e.id in seen:
                continue
            seen.add(e.id)
            out.append(e)
    return out
