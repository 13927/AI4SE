from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import ExtractResult
from .util import sanitize_path


def _findall(text: str, pattern: str) -> list[str]:
    return re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL)


def extract(repo_root: Path) -> ExtractResult:
    """
    Maven extractor v1（最小可用）：
    - 识别多模块：<modules><module>...</module></modules>
    - 产出 extra_modules：build/maven + build/maven/<module>
    说明：依赖图/packaging 等更深解析放到后续版本。
    """
    pom = repo_root / "pom.xml"
    if not pom.exists():
        return ExtractResult()

    text = pom.read_text(encoding="utf-8", errors="replace")
    modules = _findall(text, r"<module>\s*([^<\s]+)\s*</module>")

    # v1：外部依赖（上限，避免过度膨胀）
    deps: list[tuple[str, str]] = []
    dep_blocks = _findall(text, r"<dependency>\s*(.*?)\s*</dependency>")
    for blk in dep_blocks:
        g = _findall(blk, r"<groupId>\s*([^<]+)\s*</groupId>")
        a = _findall(blk, r"<artifactId>\s*([^<]+)\s*</artifactId>")
        if not g or not a:
            continue
        deps.append((g[0].strip(), a[0].strip()))
        if len(deps) >= 30:
            break

    extra = ["build/maven"]
    module_depends_on: dict[str, list[str]] = {}

    ext_mods: list[str] = []
    for g, a in deps:
        mid = "ext/maven/" + sanitize_path(g, a)
        ext_mods.append(mid)
    if ext_mods:
        module_depends_on["build/maven"] = sorted(set(ext_mods))

    for m in modules:
        mid = f"build/maven/{m}".replace("\\", "/").lower()
        extra.append(mid)
        if ext_mods:
            module_depends_on[mid] = sorted(set(ext_mods))

    extra.extend(ext_mods)
    return ExtractResult(extra_modules=sorted(set(extra)), module_depends_on=module_depends_on)
