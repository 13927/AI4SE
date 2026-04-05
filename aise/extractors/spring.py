from __future__ import annotations

import re
from pathlib import Path

from .base import Entrypoint, ExtractResult


def _java_fqcn_from_path(repo_root: Path, p: Path) -> str:
    rel = p.relative_to(repo_root).as_posix()
    # typical: src/main/java/com/x/App.java -> com.x.App
    if "/src/main/java/" in rel:
        rel = rel.split("/src/main/java/", 1)[1]
    if rel.endswith(".java"):
        rel = rel[:-5]
    return rel.replace("/", ".")


def extract(repo_root: Path) -> ExtractResult:
    """
    Spring Boot extractor v1（最小可用）：
    - 在 src/main/java 下搜索 @SpringBootApplication
    - 若文件包含 main 方法，认为是启动入口
    """
    base = repo_root / "src/main/java"
    if not base.exists():
        return ExtractResult()

    entrypoints: list[Entrypoint] = []
    for p in base.rglob("*.java"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "@SpringBootApplication" not in text:
            continue
        if re.search(r"public\s+static\s+void\s+main\s*\(", text) is None:
            continue
        fqcn = _java_fqcn_from_path(repo_root, p)
        entrypoints.append(
            Entrypoint(
                id=f"java.spring.boot.{fqcn}",
                type="other",
                match_kind="literal",
                match_value=fqcn,
                summary=f"Spring Boot main: {fqcn}",
                modules=["app/java", "build/maven"],
                source_kind="file",
                source_ref=p.relative_to(repo_root).as_posix(),
            )
        )
        break  # v1：只取一个主入口

    return ExtractResult(entrypoints=entrypoints)

