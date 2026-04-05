from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .base import ExtractResult
from .util import sanitize_path


_re_main_mapping = re.compile(r"@RequestMapping\s*\((.*?)\)", re.DOTALL)
_re_value = re.compile(r"(?:value\s*=\s*)?\"([^\"]+)\"")
_re_method = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)")


def _java_fqcn_from_path(repo_root: Path, p: Path) -> str:
    rel = p.relative_to(repo_root).as_posix()
    if "/src/main/java/" in rel:
        rel = rel.split("/src/main/java/", 1)[1]
    if rel.endswith(".java"):
        rel = rel[:-5]
    return rel.replace("/", ".")


def _extract_paths_from_args(arg: str) -> list[str]:
    # 取第一个或多个字符串 literal
    return [m.group(1) for m in _re_value.finditer(arg)]


def _extract_methods_from_args(arg: str) -> list[str]:
    return [m.group(1).upper() for m in _re_method.finditer(arg)]


def extract(repo_root: Path) -> list[dict]:
    """
    Java REST extractor v1（regex，够用即可）：
    - @RestController/@Controller + (@GetMapping/@PostMapping/...) / @RequestMapping(method=...)
    - 产出最小 API 条目 dict（符合 api.schema.json）
    """
    base = repo_root / "src/main/java"
    if not base.exists():
        return []

    apis: list[dict] = []

    for p in base.rglob("*.java"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "@RestController" not in text and "@Controller" not in text:
            continue

        fqcn = _java_fqcn_from_path(repo_root, p)

        # class-level base path
        class_base = ""
        m = _re_main_mapping.search(text)
        if m:
            paths = _extract_paths_from_args(m.group(1))
            if paths:
                class_base = paths[0]

        # method-level mappings (very rough)
        for http, anno in [
            ("GET", "@GetMapping"),
            ("POST", "@PostMapping"),
            ("PUT", "@PutMapping"),
            ("DELETE", "@DeleteMapping"),
            ("PATCH", "@PatchMapping"),
        ]:
            for mm in re.finditer(re.escape(anno) + r"\s*\((.*?)\)", text, flags=re.DOTALL):
                paths = _extract_paths_from_args(mm.group(1))
                sub = paths[0] if paths else ""
                full = (class_base.rstrip("/") + "/" + sub.lstrip("/")).rstrip("/") or "/"
                api_id = "api/java/http/" + sanitize_path(http.lower(), full)
                apis.append(
                    {
                        "kind": "api",
                        "id": api_id,
                        "name": f"{http} {full}",
                        "summary": f"Java REST endpoint {http} {full}",
                        "protocol": "http",
                        "http": {
                            "method": http,
                            "path": full,
                            "handler": {"file": p.relative_to(repo_root).as_posix(), "symbol": fqcn},
                        },
                        "related_modules": ["app/java"],
                        "provenance": {
                            "sources": [{"kind": "file", "ref": p.relative_to(repo_root).as_posix()}],
                            "last_verified_commit": "",
                            "confidence": "low"
                        },
                    }
                )

        # @RequestMapping(method=..., value="...")
        for mm in re.finditer(r"@RequestMapping\s*\((.*?)\)", text, flags=re.DOTALL):
            arg = mm.group(1)
            methods = _extract_methods_from_args(arg) or ["GET"]
            paths = _extract_paths_from_args(arg) or [""]
            for http in methods:
                for sub in paths:
                    full = (class_base.rstrip("/") + "/" + sub.lstrip("/")).rstrip("/") or "/"
                    api_id = "api/java/http/" + sanitize_path(http.lower(), full)
                    apis.append(
                        {
                            "kind": "api",
                            "id": api_id,
                            "name": f"{http} {full}",
                            "summary": f"Java REST endpoint {http} {full}",
                            "protocol": "http",
                            "http": {
                                "method": http,
                                "path": full,
                                "handler": {"file": p.relative_to(repo_root).as_posix(), "symbol": fqcn},
                            },
                            "related_modules": ["app/java"],
                            "provenance": {
                                "sources": [{"kind": "file", "ref": p.relative_to(repo_root).as_posix()}],
                                "last_verified_commit": "",
                                "confidence": "low"
                            },
                        }
                    )

    # 去重：按 id 去重
    uniq: dict[str, dict] = {}
    for a in apis:
        uniq.setdefault(a["id"], a)
    return list(uniq.values())[:200]

