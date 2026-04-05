from __future__ import annotations

from pathlib import Path

from .util import sanitize_path


def extract(repo_root: Path) -> list[dict]:
    """
    C++ Header API extractor v1：
    - 优先 include/**；如果不存在则尝试 googletest/include 与 googlemock/include
    - 每个 header 文件生成一个 api 条目（接口面清单，非符号级）
    """
    candidates: list[Path] = []
    for d in ["include", "googletest/include", "googlemock/include"]:
        p = repo_root / d
        if p.exists():
            candidates.append(p)

    apis: list[dict] = []
    for base in candidates:
        for hp in base.rglob("*.h"):
            rel = hp.relative_to(repo_root).as_posix()
            include_path = rel
            if "/include/" in rel:
                include_path = rel.split("/include/", 1)[1]

            api_id = "api/cpp/header/" + sanitize_path(include_path)
            # related_modules：尽量指向仓库内真实模块（避免引用不存在的 cpp/include）
            if rel.startswith("googletest/"):
                related = ["cpp/googletest"]
            elif rel.startswith("googlemock/"):
                related = ["cpp/googlemock"]
            elif rel.startswith("include/"):
                related = ["cpp/include"]
            else:
                related = ["core"]
            apis.append(
                {
                    "kind": "api",
                    "id": api_id,
                    "name": f"#include <{include_path}>",
                    "summary": f"C/C++ public header: {include_path}",
                    "protocol": "cpp-header",
                    "cpp_header": {"include_path": include_path, "file": rel},
                    "related_modules": related,
                    "provenance": {
                        "sources": [{"kind": "file", "ref": rel}],
                        "last_verified_commit": "",
                        "confidence": "low"
                    },
                }
            )

    uniq: dict[str, dict] = {}
    for a in apis:
        uniq.setdefault(a["id"], a)
    return list(uniq.values())[:300]
