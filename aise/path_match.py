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

