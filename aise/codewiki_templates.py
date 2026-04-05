from __future__ import annotations

import json
from pathlib import Path


def ensure_dirs(root: Path) -> None:
    (root / "L1/modules").mkdir(parents=True, exist_ok=True)
    (root / "L1/apis").mkdir(parents=True, exist_ok=True)
    (root / "L1/flows").mkdir(parents=True, exist_ok=True)
    (root / "L1/playbooks").mkdir(parents=True, exist_ok=True)
    (root / "L1/relations").mkdir(parents=True, exist_ok=True)
    (root / "L2/modules").mkdir(parents=True, exist_ok=True)
    (root / "L2/apis").mkdir(parents=True, exist_ok=True)
    (root / "L2/flows").mkdir(parents=True, exist_ok=True)
    (root / "L2/playbooks").mkdir(parents=True, exist_ok=True)
    (root / "views").mkdir(parents=True, exist_ok=True)
    (root / "model/schemas").mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_views_entrypoints(command_name: str) -> dict:
    return {
        "kind": "view.entrypoints",
        "version": "0.1",
        "entrypoints": [
            {
                "id": "cli.default",
                "type": "cli",
                "match": {"kind": "literal", "value": command_name},
                "summary": "默认 CLI 入口",
                "targets": {"modules": ["cli"]},
                "source": {"kind": "other", "ref": "manual"},
            }
        ],
    }


def default_views_filetree(src_root: str = "src") -> dict:
    # 兜底优先：先粗后细，后续 scan 会补更细规则。
    return {
        "kind": "view.filetree",
        "version": "0.1",
        "roots": [src_root],
        "mappings": [
            {
                "id": f"{src_root}.all",
                "match": {"kind": "glob", "value": f"{src_root}/**"},
                "summary": f"兜底映射：所有 {src_root} 文件先归到 core（后续逐步细分）",
                "targets": {"modules": ["core"]},
                "priority": 1,
                "source": {"kind": "other", "ref": "manual"},
            }
            ,
            {
                "id": "repo.all",
                "match": {"kind": "glob", "value": "**"},
                "summary": "兜底映射：仓库内任意文件默认归到 core（用于 diff 校验与增量定位）",
                "targets": {"modules": ["core"]},
                "priority": 0,
                "source": {"kind": "other", "ref": "manual"},
            }
        ],
    }


def default_l1_index() -> dict:
    return {
        "kind": "index",
        "version": "0.1",
        "modules": [],
        "apis": [],
        "flows": [],
        "playbooks": [],
        "relations": [],
    }
