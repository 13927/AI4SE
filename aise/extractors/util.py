from __future__ import annotations

import re


def sanitize_segment(seg: str) -> str:
    """
    将任意字符串压缩成符合 moduleId/apiId 规则的 segment：
    - 仅允许 [a-z0-9-]
    """
    s = seg.strip().lower()
    s = s.replace(".", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "x"


def sanitize_path(*segs: str) -> str:
    return "/".join(sanitize_segment(s) for s in segs if s is not None and str(s).strip() != "")

