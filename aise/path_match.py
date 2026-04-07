from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MatchRule:
    kind: str  # glob|regex|prefix|literal
    value: str

    def matches(self, path: str) -> bool:
        p = path.replace("\\", "/")
        if self.kind == "literal":
            return p == self.value
        if self.kind == "prefix":
            return p.startswith(self.value)
        if self.kind == "glob":
            return fnmatch.fnmatch(p, self.value)
        if self.kind == "regex":
            return re.search(self.value, p) is not None
        raise ValueError(f"未知 match.kind: {self.kind}")


def match_glob(path: str, pattern: str) -> bool:
    """
    轻量 glob 匹配（用于 ignore / scopes 等），对路径分隔符做归一化。
    注意：这里依赖 Python fnmatch 的行为（支持 * ? []，并把 / 当作普通字符处理）。
    """
    p = path.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    return fnmatch.fnmatch(p, pat)
